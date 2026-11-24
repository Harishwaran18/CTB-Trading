"""Web portal for CTB-Trading.

Serves a live dashboard and a small JSON API on top of the same engine the bot
uses, so what you see in the browser is exactly what the bot would trade.

    python main.py portal --port 12000
    # or: uvicorn portal:app --host 0.0.0.0 --port 12000
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from backtest import run_backtest
from config import STRATEGIES, load_settings
from market_data import MarketDataError, get_candles
from models import Signal, interval_seconds
from signal_engine import compute_features, get_signal
from storage import StateStore
from symbols import available, resolve

log = logging.getLogger("ctb.portal")

STATIC_DIR = Path(__file__).resolve().parent / "static"
app = FastAPI(title="CTB-Trading Portal", version="1.0.0")

_settings = load_settings()
_scan_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_market_cache: dict[str, tuple[float, dict]] = {}
_lock = asyncio.Lock()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _json_safe(value: Any) -> Any:
    """Recursively replace NaN/inf (invalid JSON) with None."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _signal_dict(sig: Signal) -> dict:
    return {
        "symbol": sig.symbol,
        "asset_class": sig.asset_class,
        "interval": sig.interval,
        "direction": sig.direction.value,
        "binary": sig.binary_direction,
        "confidence": round(sig.confidence, 1),
        "score": round(sig.score, 1),
        "regime": sig.regime,
        "price": sig.price,
        "entry": sig.entry,
        "stop_loss": sig.stop_loss,
        "take_profit": sig.take_profit,
        "risk_reward": sig.risk_reward,
        "atr": sig.atr,
        "higher_direction": sig.higher_direction.value,
        "actionable": sig.is_actionable,
        "reasons": sig.reasons,
        "indicators": _json_safe(sig.indicators),
        "candle_time": sig.candle_time,
    }


def _scan() -> list[dict]:
    out: list[dict] = []
    for name in _settings.symbols:
        try:
            sig = get_signal(name, settings=_settings)
            if sig is None:
                out.append({"symbol": name, "error": "insufficient or stale data"})
            else:
                out.append(_signal_dict(sig))
        except Exception as exc:
            log.warning("scan %s failed: %s", name, exc)
            out.append({"symbol": name, "error": str(exc)[:160]})
    return out


def _rows(df) -> list[dict]:
    return [
        {
            "t": int(ts.timestamp()),
            "o": round(float(row["open"]), 8),
            "h": round(float(row["high"]), 8),
            "l": round(float(row["low"]), 8),
            "c": round(float(row["close"]), 8),
            "v": round(float(row["volume"]), 2),
        }
        for ts, row in df.iterrows()
    ]


def _market(symbol: str, interval: str) -> dict:
    inst = resolve(symbol)
    df = get_candles(inst, interval, 260, _settings)
    feats = compute_features(df)
    row = feats.iloc[-1]
    sig = get_signal(symbol, interval=interval, settings=_settings)
    indicators = {
        k: _json_safe(round(float(v), 6))
        for k, v in row.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    return {
        "symbol": inst.name,
        "asset_class": inst.asset_class,
        "interval": interval,
        "candles": _rows(df.tail(220)),
        "indicators": indicators,
        "signal": _signal_dict(sig) if sig else None,
    }


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

@app.get("/api/health")
async def api_health() -> dict:
    return {
        "status": "ok",
        "time": int(time.time()),
        "symbols": list(_settings.symbols),
        "base_interval": _settings.base_interval,
        "higher_interval": _settings.higher_interval,
        "strategy": _settings.strategy,
        "min_confidence": _settings.min_confidence,
        "dry_run": _settings.dry_run,
        "telegram": _settings.enable_telegram,
    }


@app.get("/api/scan")
async def api_scan(refresh: bool = False) -> dict:
    now = time.time()
    if not refresh and _scan_cache["data"] is not None and now - _scan_cache["ts"] < 10:
        return {"ts": _scan_cache["ts"], "signals": _scan_cache["data"], "cached": True}
    async with _lock:
        data = await asyncio.to_thread(_scan)
        _scan_cache.update(ts=time.time(), data=data)
    return {"ts": time.time(), "signals": data, "cached": False}


@app.get("/api/performance")
async def api_performance(limit: int = Query(25, ge=1, le=200)) -> dict:
    def work() -> dict:
        store = StateStore(_settings.db_path)
        try:
            return {"overall": store.performance(), "recent": store.recent_signals(limit)}
        finally:
            store.close()

    return await asyncio.to_thread(work)


@app.get("/api/symbols")
async def api_symbols() -> dict:
    return {"symbols": available()}


@app.get("/api/market/{symbol:path}")
async def api_market(symbol: str, interval: str | None = None) -> dict:
    interval = interval or _settings.base_interval
    try:
        interval_seconds(interval)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"unsupported interval {interval}")
    key = f"{symbol}:{interval}"
    hit = _market_cache.get(key)
    if hit and time.time() - hit[0] < 20:
        return hit[1]
    try:
        data = await asyncio.to_thread(_market, symbol, interval)
    except MarketDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    _market_cache[key] = (time.time(), data)
    return data


@app.get("/api/backtest")
async def api_backtest(
    symbol: str = "EUR/USD",
    interval: str = "15m",
    bars: int = Query(2000, ge=300, le=5000),
    strategy: str = "adaptive",
    horizon: int | None = None,
) -> dict:
    if strategy not in STRATEGIES:
        raise HTTPException(status_code=400, detail="unknown strategy")
    try:
        interval_seconds(interval)
    except ValueError:
        raise HTTPException(status_code=400, detail="unsupported interval")
    settings = replace(_settings, strategy=strategy)
    try:
        report = await asyncio.to_thread(
            lambda: run_backtest(symbol, interval, bars, horizon=horizon, settings=settings)
        )
    except MarketDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return _json_safe({
        "summary": report.summary(),
        "buckets": report._confidence_buckets(),
        "text": report.to_text(),
        "directional": round(report.directional_accuracy(), 1),
    })


@app.get("/favicon.ico")
async def favicon() -> Response:
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def serve(host: str = "0.0.0.0", port: int = 12000, reload: bool = False) -> None:
    import uvicorn

    uvicorn.run("portal:app", host=host, port=port, reload=reload, log_level="info")


if __name__ == "__main__":
    serve()
