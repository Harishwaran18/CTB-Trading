"""Market data for crypto, forex and stocks.

Providers are tried in order until one returns usable candles, so a single
blocked or rate-limited endpoint never stops the bot:

    crypto : Coinbase -> Kraken -> TwelveData -> Yahoo
    forex  : TwelveData -> Yahoo
    stocks : TwelveData -> Yahoo

Every function returns a DataFrame indexed by UTC timestamp with the columns
``open, high, low, close, volume`` sorted oldest-first.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import pandas as pd
import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import Settings, load_settings
from models import Symbol, interval_seconds
from symbols import resolve

log = logging.getLogger("ctb.data")

COLUMNS = ["open", "high", "low", "close", "volume"]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}

_session = requests.Session()
_session.headers.update(_HEADERS)


class MarketDataError(RuntimeError):
    """Raised when every provider for a symbol fails."""


class _Transient(Exception):
    """Retryable transport / server error."""


@retry(
    retry=retry_if_exception_type(_Transient),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4),
    reraise=True,
)
def _get_json(url: str, params: dict | None, timeout: float) -> dict:
    try:
        response = _session.get(url, params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise _Transient(str(exc)) from exc
    if response.status_code == 429 or response.status_code >= 500:
        raise _Transient(f"HTTP {response.status_code} from {url}")
    if response.status_code >= 400:
        raise MarketDataError(f"HTTP {response.status_code} from {url}")
    try:
        return response.json()
    except ValueError as exc:
        raise MarketDataError(f"Non-JSON response from {url}") from exc


def _frame(rows: list[dict], index) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=COLUMNS)
    for column in COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame.index = pd.DatetimeIndex(index, name="time")
    frame = frame[~frame.index.duplicated(keep="last")]
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    return frame.sort_index()


# --------------------------------------------------------------------------- #
# Yahoo Finance - universal fallback, no API key, covers all three markets.
# --------------------------------------------------------------------------- #

_YAHOO_INTERVAL = {
    "1m": "1m", "2m": "2m", "5m": "5m", "15m": "15m",
    "30m": "30m", "1h": "60m", "1d": "1d",
}
_YAHOO_RANGE = {
    "1m": "5d", "2m": "1mo", "5m": "1mo", "15m": "1mo",
    "30m": "1mo", "1h": "3mo", "1d": "2y",
}


def _fetch_yahoo(symbol: Symbol, interval: str, limit: int, timeout: float) -> pd.DataFrame:
    if interval not in _YAHOO_INTERVAL:
        raise MarketDataError(f"Yahoo does not serve {interval} candles")
    payload = _get_json(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol.yahoo}",
        {
            "interval": _YAHOO_INTERVAL[interval],
            "range": _YAHOO_RANGE[interval],
            "includePrePost": "false",
        },
        timeout,
    )
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise MarketDataError(f"Yahoo error: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise MarketDataError("Yahoo returned no result")
    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    if not timestamps or not closes:
        raise MarketDataError("Yahoo returned an empty series")

    rows = [
        {
            "open": o, "high": h, "low": low, "close": c,
            "volume": v if v is not None else 0.0,
        }
        for o, h, low, c, v in zip(
            quote.get("open", []),
            quote.get("high", []),
            quote.get("low", []),
            closes,
            quote.get("volume", []),
        )
    ]
    index = pd.to_datetime(timestamps, unit="s", utc=True)
    return _frame(rows, index).tail(limit)


# --------------------------------------------------------------------------- #
# Coinbase - free, keyless, up to 300 candles per call.
# --------------------------------------------------------------------------- #

_COINBASE_GRANULARITY = {
    "1m": "ONE_MINUTE", "5m": "FIVE_MINUTE", "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE", "1h": "ONE_HOUR", "2h": "TWO_HOUR",
    "6h": "SIX_HOUR", "1d": "ONE_DAY",
}


def _fetch_coinbase(symbol: Symbol, interval: str, limit: int, timeout: float) -> pd.DataFrame:
    granularity = _COINBASE_GRANULARITY.get(interval)
    if not granularity or not symbol.coinbase:
        raise MarketDataError(f"Coinbase does not serve {symbol.name} {interval}")
    payload = _get_json(
        f"https://api.coinbase.com/api/v3/brokerage/market/products/"
        f"{symbol.coinbase}/candles",
        {"granularity": granularity, "limit": min(limit, 300)},
        timeout,
    )
    candles = payload.get("candles") or []
    if not candles:
        raise MarketDataError("Coinbase returned no candles")
    rows = [
        {"open": c["open"], "high": c["high"], "low": c["low"],
         "close": c["close"], "volume": c.get("volume", 0.0)}
        for c in candles
    ]
    index = pd.to_datetime([int(c["start"]) for c in candles], unit="s", utc=True)
    return _frame(rows, index).tail(limit)


# --------------------------------------------------------------------------- #
# Kraken - free, keyless.
# --------------------------------------------------------------------------- #

_KRAKEN_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440,
}


def _fetch_kraken(symbol: Symbol, interval: str, limit: int, timeout: float) -> pd.DataFrame:
    minutes = _KRAKEN_MINUTES.get(interval)
    if not minutes or not symbol.kraken:
        raise MarketDataError(f"Kraken does not serve {symbol.name} {interval}")
    payload = _get_json(
        "https://api.kraken.com/0/public/OHLC",
        {"pair": symbol.kraken, "interval": minutes},
        timeout,
    )
    if payload.get("error"):
        raise MarketDataError(f"Kraken error: {payload['error']}")
    result = payload.get("result") or {}
    series = next(
        (value for key, value in result.items() if key != "last" and isinstance(value, list)),
        None,
    )
    if not series:
        raise MarketDataError("Kraken returned no candles")
    rows = [
        {"open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[6]}
        for r in series
    ]
    index = pd.to_datetime([int(r[0]) for r in series], unit="s", utc=True)
    return _frame(rows, index).tail(limit)


# --------------------------------------------------------------------------- #
# TwelveData - optional key, clean intraday forex/stock candles.
# --------------------------------------------------------------------------- #

_TWELVEDATA_INTERVAL = {
    "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "4h": "4h", "1d": "1day",
}


def _fetch_twelvedata(
    symbol: Symbol, interval: str, limit: int, timeout: float, api_key: str
) -> pd.DataFrame:
    twelve_interval = _TWELVEDATA_INTERVAL.get(interval)
    if not twelve_interval or not symbol.twelvedata:
        raise MarketDataError(f"TwelveData does not serve {symbol.name} {interval}")
    payload = _get_json(
        "https://api.twelvedata.com/time_series",
        {
            "symbol": symbol.twelvedata,
            "interval": twelve_interval,
            "outputsize": min(max(limit, 50), 5000),
            "timezone": "UTC",
            "order": "ASC",
            "apikey": api_key,
        },
        timeout,
    )
    if payload.get("status") == "error" or payload.get("code"):
        raise MarketDataError(f"TwelveData error: {payload.get('message')}")
    values = payload.get("values") or []
    if not values:
        raise MarketDataError("TwelveData returned no values")
    rows = [
        {"open": v["open"], "high": v["high"], "low": v["low"],
         "close": v["close"], "volume": v.get("volume", 0.0)}
        for v in values
    ]
    index = pd.to_datetime([v["datetime"] for v in values], utc=True)
    return _frame(rows, index).tail(limit)


# --------------------------------------------------------------------------- #
# Cache + orchestration
# --------------------------------------------------------------------------- #

_cache: dict[tuple, tuple[float, pd.DataFrame]] = {}
_cache_lock = threading.Lock()


def _cache_get(key: tuple, ttl: float) -> pd.DataFrame | None:
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1].copy()
    return None


def _cache_put(key: tuple, frame: pd.DataFrame, ttl: float) -> None:
    with _cache_lock:
        _cache[key] = (time.time() + ttl, frame.copy())


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


_FETCHERS = {
    "yahoo": lambda s, i, n, t, k: _fetch_yahoo(s, i, n, t),
    "coinbase": lambda s, i, n, t, k: _fetch_coinbase(s, i, n, t),
    "kraken": lambda s, i, n, t, k: _fetch_kraken(s, i, n, t),
    "twelvedata": lambda s, i, n, t, k: _fetch_twelvedata(s, i, n, t, k),
}


def get_candles(
    symbol: str | Symbol,
    interval: str | None = None,
    limit: int | None = None,
    settings: Settings | None = None,
    min_rows: int = 0,
) -> pd.DataFrame:
    """Fetch candles for ``symbol`` using the provider fallback chain.

    ``min_rows`` skips providers that cannot supply enough history (useful for
    backtests), falling through to one that can.
    """
    settings = settings or load_settings()
    instrument = resolve(symbol) if isinstance(symbol, str) else symbol
    interval = interval or settings.base_interval
    limit = limit or settings.candle_limit

    key = (instrument.name, interval, limit)
    cached = _cache_get(key, settings.cache_ttl_seconds)
    if cached is not None and len(cached) >= min_rows:
        return cached

    chain = list(instrument.providers)
    errors: list[str] = []
    for position, provider in enumerate(chain):
        if provider == "twelvedata" and not settings.twelvedata_api_key:
            continue
        try:
            frame = _FETCHERS[provider](
                instrument, interval, limit, settings.request_timeout,
                settings.twelvedata_api_key,
            )
        except Exception as exc:  # provider failure -> try next
            errors.append(f"{provider}: {exc}")
            log.debug("%s %s via %s failed: %s", instrument.name, interval, provider, exc)
            continue
        if frame is None or len(frame) < 2:
            errors.append(f"{provider}: insufficient data")
            continue
        is_last = position == len(chain) - 1
        if min_rows and len(frame) < min_rows and not is_last:
            errors.append(f"{provider}: only {len(frame)} < {min_rows} rows")
            log.debug(
                "%s %s: %s returned %d rows, need %d; trying next provider",
                instrument.name, interval, provider, len(frame), min_rows,
            )
            continue
        log.debug(
            "%s %s <- %s (%d candles)", instrument.name, interval, provider, len(frame)
        )
        _cache_put(key, frame, settings.cache_ttl_seconds)
        return frame

    raise MarketDataError(
        f"No provider returned {instrument.name} {interval}. Tried: "
        + "; ".join(errors or ["none available"])
    )


def only_closed(
    frame: pd.DataFrame, interval: str, now: datetime | None = None
) -> pd.DataFrame:
    """Drop the still-forming candle so signals use closed data only."""
    if frame.empty:
        return frame
    now = now or datetime.now(timezone.utc)
    cutoff = pd.Timestamp(now).tz_convert("UTC")
    close_times = frame.index + pd.Timedelta(seconds=interval_seconds(interval))
    return frame[close_times <= cutoff]


def last_candle_time(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    return int(frame.index[-1].timestamp())


def is_stale(frame: pd.DataFrame, interval: str, now: datetime | None = None) -> bool:
    """True when the newest candle is older than three intervals (market shut)."""
    if frame.empty:
        return True
    now = now or datetime.now(timezone.utc)
    age = pd.Timestamp(now).tz_convert("UTC") - frame.index[-1]
    return age > pd.Timedelta(seconds=3 * interval_seconds(interval))


# --------------------------------------------------------------------------- #
# Backwards-compatible helper kept for older scripts.
# --------------------------------------------------------------------------- #

def get_forex_data(symbol: str = "EUR/USD", outputsize: int = 200) -> pd.DataFrame | None:
    try:
        return get_candles(symbol, "1m", outputsize)
    except Exception as exc:
        log.error("get_forex_data failed: %s", exc)
        return None
