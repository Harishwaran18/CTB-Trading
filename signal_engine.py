"""Multi-timeframe, regime-adaptive confluence signal engine.

Design
------
Each candle produces a set of independent component *votes* in ``[-1, +1]``:
trend structure, higher-timeframe bias, MACD, RSI, Bollinger position,
stochastic, volume expansion, swing structure and a trend-pullback setup.

The market regime (trending vs ranging) re-weights those votes:

* **Trending** (ADX high): momentum and pullback components dominate; RSI and
  Bollinger are read as trend continuation.
* **Ranging** (ADX low): mean-reversion components dominate; RSI and Bollinger
  are read as over-extension to fade.

That regime switch is the core of the edge: it stops the bot buying the top of
a range or fading a genuine trend. The weighted vote is normalised to a
``[-100, +100]`` score, and a signal is only actionable when ``|score|`` clears
``settings.min_confidence``.

Honesty
-------
No indicator combination gives 90% directional accuracy. On liquid markets,
short-horizon hit rates sit near 50-58%. Run ``backtest.py`` on *your* market
and timeframe to measure the real number; only trade setups that show a
positive expectancy there.

``evaluate()`` is pure and shared by live trading and the backtester, so the
accuracy you measure is the accuracy you get live.
"""

from __future__ import annotations

import logging
import math

import pandas as pd

import indicators as ind
from config import Settings, load_settings
from market_data import get_candles, only_closed
from models import Direction, Regime, Signal, Symbol
from risk import build_levels
from symbols import resolve

log = logging.getLogger("ctb.signal")

# EMA200 plus Bollinger-width history needs a long runway before it is valid.
WARMUP = 210

_NEXT_HIGHER = {
    "1m": "5m", "2m": "15m", "5m": "15m", "15m": "1h",
    "30m": "4h", "1h": "4h", "2h": "1d", "4h": "1d", "1d": "1d",
}

TRENDING_MULT = {
    "trend": 1.40, "higher_tf": 1.00, "macd": 1.30, "rsi": 0.70,
    "bollinger": 0.60, "stochastic": 0.60, "volume": 1.00,
    "structure": 1.20, "pullback": 1.30,
}
RANGING_MULT = {
    "trend": 0.40, "higher_tf": 0.60, "macd": 0.60, "rsi": 1.60,
    "bollinger": 1.60, "stochastic": 1.30, "volume": 0.80,
    "structure": 0.50, "pullback": 0.40,
}


def next_higher(interval: str) -> str:
    return _NEXT_HIGHER.get(interval, interval)


def _f(row, key: str, default: float = 0.0) -> float:
    try:
        value = float(row[key])
    except (KeyError, IndexError, TypeError, ValueError):
        return default
    return default if math.isnan(value) else value


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


# --------------------------------------------------------------------------- #
# Feature computation (causal)
# --------------------------------------------------------------------------- #

def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Attach every indicator the scorer needs. Row *i* uses only rows <= *i*."""
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float) if "volume" in df else pd.Series(0.0, index=df.index)

    out = pd.DataFrame(index=df.index)
    out["open"] = df["open"].astype(float)
    out["high"] = high
    out["low"] = low
    out["close"] = close
    out["volume"] = volume

    out["ema20"] = ind.ema(close, 20)
    out["ema50"] = ind.ema(close, 50)
    out["ema200"] = ind.ema(close, 200)
    out["rsi"] = ind.rsi(close, 14)

    macd_line, macd_signal, macd_hist = ind.macd(close)
    out["macd"] = macd_line
    out["macd_signal"] = macd_signal
    out["macd_hist"] = macd_hist

    out["atr"] = ind.atr(high, low, close, 14)

    bb_upper, bb_mid, bb_lower, bb_width = ind.bollinger(close, 20, 2.0)
    out["bb_upper"] = bb_upper
    out["bb_mid"] = bb_mid
    out["bb_lower"] = bb_lower
    out["bb_width"] = bb_width
    out["bb_width_ma"] = bb_width.rolling(50, min_periods=20).mean()

    stoch_k, stoch_d = ind.stochastic(high, low, close, 14, 3)
    out["stoch_k"] = stoch_k
    out["stoch_d"] = stoch_d

    adx_value, plus_di, minus_di = ind.adx(high, low, close, 14)
    out["adx"] = adx_value
    out["plus_di"] = plus_di
    out["minus_di"] = minus_di

    out["vol_ma"] = volume.rolling(20, min_periods=5).mean()
    out["structure"] = ind.swing_structure(high, low, 10)
    out["roc"] = ind.roc(close, 10)
    return out


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

def regime_of(row) -> Regime:
    return Regime.TRENDING if _f(row, "adx", 0.0) >= 22.0 else Regime.RANGING


def _multipliers(regime: Regime, strategy: str) -> dict[str, float]:
    if strategy == "trend":
        return TRENDING_MULT
    if strategy == "reversion":
        return RANGING_MULT
    return TRENDING_MULT if regime is Regime.TRENDING else RANGING_MULT


def _component_votes(row, prev_row, regime: Regime) -> dict[str, float]:
    close = _f(row, "close")
    ema20, ema50, ema200 = _f(row, "ema20"), _f(row, "ema50"), _f(row, "ema200")
    votes: dict[str, float] = {}

    # 1. EMA stack alignment.
    checks = [ema20 > ema50, ema50 > ema200, close > ema200]
    votes["trend"] = (sum(checks) - (3 - sum(checks))) / 3.0

    # 2. MACD: line vs signal, histogram slope, zero-line side.
    macd, signal = _f(row, "macd"), _f(row, "macd_signal")
    hist, prev_hist = _f(row, "macd_hist"), _f(prev_row, "macd_hist")
    votes["macd"] = _clip(
        0.5 * (1 if macd > signal else -1)
        + 0.3 * (1 if hist > prev_hist else -1)
        + 0.2 * (1 if macd > 0 else -1)
    )

    # 3. RSI: continuation in trends, over-extension to fade in ranges.
    rsi = _f(row, "rsi", 50.0)
    if regime is Regime.TRENDING:
        votes["rsi"] = _clip((rsi - 50.0) / 20.0)
    else:
        votes["rsi"] = _clip((50.0 - rsi) / 20.0)

    # 4. Bollinger position.
    mid, upper = _f(row, "bb_mid"), _f(row, "bb_upper")
    half = max(upper - mid, 1e-12)
    if regime is Regime.TRENDING:
        votes["bollinger"] = _clip((close - mid) / half)
    else:
        votes["bollinger"] = _clip((mid - close) / half)

    # 5. Stochastic, amplified in exhaustion zones.
    k, d = _f(row, "stoch_k", 50.0), _f(row, "stoch_d", 50.0)
    zone = 0.3 if k < 20 else (-0.3 if k > 80 else 0.0)
    votes["stochastic"] = _clip(0.7 * (1 if k > d else -1) + zone)

    # 6. Volume expansion in the direction of the candle body.
    vol_ma, volume = _f(row, "vol_ma"), _f(row, "volume")
    expansion = (volume / vol_ma) if vol_ma > 0 else 0.0
    if expansion >= 1.2:
        body_up = close >= _f(row, "open")
        votes["volume"] = _clip((1 if body_up else -1) * min(expansion / 2.0, 1.0))
    else:
        votes["volume"] = 0.0

    # 7. Swing structure.
    votes["structure"] = _clip(_f(row, "structure"))

    # 8. Trend pullback: buy a dip in an uptrend, sell a bounce in a downtrend.
    adx_value = _f(row, "adx")
    votes["pullback"] = 0.0
    if adx_value > 20:
        if ema50 > ema200 and close > ema200 and 38.0 <= rsi <= 52.0 and close <= ema20:
            votes["pullback"] = 1.0
        elif ema50 < ema200 and close < ema200 and 48.0 <= rsi <= 62.0 and close >= ema20:
            votes["pullback"] = -1.0
    return votes


def _weighted_score(
    votes: dict[str, float], row, settings: Settings, strategy: str
) -> tuple[float, Regime]:
    regime = regime_of(row)
    mult = _multipliers(regime, strategy)
    numerator = 0.0
    denominator = 0.0
    for name, vote in votes.items():
        effective = settings.weights[name] * mult[name]
        numerator += effective * vote
        denominator += effective
    score = (numerator / denominator * 100.0) if denominator else 0.0

    # Dampen confidence in a dead-flat tape where ATR collapses.
    close = _f(row, "close")
    atr_value = _f(row, "atr")
    if close > 0 and atr_value / close < 0.0002:
        score *= 0.85
    return _clip(score, -100.0, 100.0), regime


def raw_score(row, prev_row, settings: Settings, strategy: str = "adaptive") -> float:
    """Directional score without the higher-timeframe term (used for HTF bias)."""
    votes = _component_votes(row, prev_row, regime_of(row))
    score, _ = _weighted_score(votes, row, settings, strategy)
    return score


def evaluate(
    row, prev_row, htf_score: float, settings: Settings, strategy: str | None = None
) -> dict:
    """Combine base components with the higher-timeframe bias."""
    strategy = strategy or settings.strategy
    votes = _component_votes(row, prev_row, regime_of(row))
    base, regime = _weighted_score(votes, row, settings, strategy)

    # Fold in the higher-timeframe bias at its own weight.
    mult = _multipliers(regime, strategy)
    htf_weight = settings.weights["higher_tf"] * mult["higher_tf"]
    base_weight = sum(settings.weights[name] * mult[name] for name in votes)
    total_weight = base_weight + htf_weight
    score = (base * base_weight + _clip(htf_score / 60.0) * 100.0 * htf_weight) / total_weight
    score = _clip(score, -100.0, 100.0)

    confidence = round(abs(score), 1)
    if score > 0:
        direction = Direction.BUY
    elif score < 0:
        direction = Direction.SELL
    else:
        direction = Direction.NEUTRAL

    actionable = (
        direction is not Direction.NEUTRAL and confidence >= settings.min_confidence
    )
    return {
        "score": round(score, 1),
        "confidence": confidence,
        "direction": direction,
        "actionable": actionable,
        "regime": regime.value,
        "reasons": _reasons(votes, row, regime, htf_score),
        "htf_score": round(htf_score, 1),
    }


def _reasons(votes: dict[str, float], row, regime: Regime, htf_score: float) -> list[str]:
    out: list[str] = []
    if votes["trend"] >= 0.66:
        out.append("EMA 20/50/200 stacked bullish")
    elif votes["trend"] <= -0.66:
        out.append("EMA 20/50/200 stacked bearish")

    if abs(htf_score) >= 20:
        side = "bullish" if htf_score > 0 else "bearish"
        out.append(f"Higher timeframe bias {side} ({htf_score:+.0f})")

    if votes["macd"] >= 0.5:
        out.append("MACD momentum up")
    elif votes["macd"] <= -0.5:
        out.append("MACD momentum down")

    rsi = _f(row, "rsi", 50.0)
    if regime is Regime.TRENDING and (rsi > 60 or rsi < 40):
        out.append(f"RSI {rsi:.0f} supports the trend")
    elif regime is Regime.RANGING and (rsi < 30 or rsi > 70):
        out.append(f"RSI {rsi:.0f} stretched for reversion")

    adx_value = _f(row, "adx")
    if adx_value >= 25:
        out.append(f"ADX {adx_value:.0f} confirms a strong trend")
    elif adx_value and adx_value < 18:
        out.append(f"ADX {adx_value:.0f} is weak (choppy)")

    vol_ma = _f(row, "vol_ma")
    if vol_ma > 0 and _f(row, "volume") / vol_ma >= 1.5:
        out.append(f"Volume surge {_f(row, 'volume') / vol_ma:.1f}x average")

    if votes["pullback"] > 0:
        out.append("Pullback entry in an uptrend")
    elif votes["pullback"] < 0:
        out.append("Pullback entry in a downtrend")

    if _f(row, "structure") > 0:
        out.append("Higher highs and higher lows")
    elif _f(row, "structure") < 0:
        out.append("Lower highs and lower lows")

    out.append(f"Regime: {regime.value}")
    return out


# --------------------------------------------------------------------------- #
# Signal construction
# --------------------------------------------------------------------------- #

def _indicator_snapshot(row) -> dict[str, float]:
    keys = ("close", "rsi", "macd", "macd_signal", "atr", "adx",
            "bb_upper", "bb_mid", "bb_lower", "stoch_k", "stoch_d")
    return {k: round(_f(row, k), 8) for k in keys}


def analyze(
    base_df: pd.DataFrame,
    htf_df: pd.DataFrame,
    symbol: Symbol,
    interval: str,
    settings: Settings,
) -> Signal | None:
    """Evaluate already-closed candle frames into a :class:`Signal`."""
    if len(base_df) < WARMUP or len(htf_df) < 30:
        log.debug("%s %s: not enough history (%d candles)", symbol.name, interval, len(base_df))
        return None

    base = compute_features(base_df)
    htf = compute_features(htf_df)

    htf_score = raw_score(htf.iloc[-1], htf.iloc[-2], settings)
    result = evaluate(base.iloc[-1], base.iloc[-2], htf_score, settings)

    row = base.iloc[-1]
    price = _f(row, "close")
    atr_value = _f(row, "atr")
    levels = build_levels(
        result["direction"], price, atr_value,
        settings.sl_atr_mult, settings.tp_atr_mult,
    )
    htf_direction = (
        Direction.BUY if htf_score > 0 else Direction.SELL if htf_score < 0 else Direction.NEUTRAL
    )

    return Signal(
        symbol=symbol.name,
        asset_class=symbol.asset_class.value,
        interval=interval,
        direction=result["direction"],
        confidence=result["confidence"],
        price=round(price, symbol.price_decimals),
        candle_time=int(base.index[-1].timestamp()),
        entry=round(levels.entry, symbol.price_decimals),
        stop_loss=round(levels.stop_loss, symbol.price_decimals),
        take_profit=round(levels.take_profit, symbol.price_decimals),
        risk_reward=levels.risk_reward,
        atr=round(atr_value, symbol.price_decimals),
        score=result["score"],
        regime=result["regime"],
        reasons=result["reasons"],
        indicators=_indicator_snapshot(row),
        higher_direction=htf_direction,
        actionable=result["actionable"],
    )


def get_signal(
    symbol: str = "EUR/USD",
    interval: str | None = None,
    settings: Settings | None = None,
) -> Signal | None:
    """Fetch data and return a signal for ``symbol`` (closed candles only)."""
    settings = settings or load_settings()
    instrument = resolve(symbol)
    base_interval = interval or settings.base_interval
    htf_interval = settings.higher_interval if interval is None else next_higher(interval)

    try:
        base = only_closed(
            get_candles(instrument, base_interval, settings=settings), base_interval
        )
        htf = only_closed(
            get_candles(instrument, htf_interval, settings=settings), htf_interval
        )
    except Exception as exc:
        log.error("%s: data fetch failed: %s", instrument.name, exc)
        return None

    return analyze(base, htf, instrument, base_interval, settings)
