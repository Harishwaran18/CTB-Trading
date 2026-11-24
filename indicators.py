"""Vectorised technical indicators.

Every function is causal: the value at row *i* only depends on rows <= *i*.
That property is what makes the backtester honest — no look-ahead bias.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False, min_periods=window).mean()


def wilder(series: pd.Series, window: int) -> pd.Series:
    """Wilder's smoothing (RMA), used by RSI / ATR / ADX."""
    return series.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = wilder(gain, window)
    avg_loss = wilder(loss, window)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # A flat/rising-only series has zero loss: RSI is 100 by definition.
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(~((avg_loss == 0) & (avg_gain == 0)), 50.0)
    return out


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return macd_line, signal_line, macd_line - signal_line


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    )
    return ranges.max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    return wilder(true_range(high, low, close), window)


def bollinger(
    close: pd.Series, window: int = 20, dev: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    mid = sma(close, window)
    std = close.rolling(window, min_periods=window).std(ddof=0)
    upper = mid + dev * std
    lower = mid - dev * std
    width = (upper - lower) / mid.replace(0.0, np.nan)
    return upper, mid, lower, width


def stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, d: int = 3
) -> tuple[pd.Series, pd.Series]:
    lowest = low.rolling(k, min_periods=k).min()
    highest = high.rolling(k, min_periods=k).max()
    span = (highest - lowest).replace(0.0, np.nan)
    percent_k = 100.0 * (close - lowest) / span
    percent_d = percent_k.rolling(d, min_periods=d).mean()
    return percent_k, percent_d


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14
) -> tuple[pd.Series, pd.Series, pd.Series]:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=high.index,
    )
    atr_ = true_range(high, low, close).ewm(
        alpha=1.0 / window, adjust=False, min_periods=window
    ).mean()
    plus_di = 100.0 * wilder(plus_dm, window) / atr_.replace(0.0, np.nan)
    minus_di = 100.0 * wilder(minus_dm, window) / atr_.replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx_ = wilder(dx, window)
    return adx_, plus_di, minus_di


def roc(close: pd.Series, window: int = 10) -> pd.Series:
    return close.pct_change(window) * 100.0


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume.fillna(0.0)).cumsum()


def swing_structure(high: pd.Series, low: pd.Series, window: int = 10) -> pd.Series:
    """+1 when making higher highs & higher lows, -1 for lower, 0 mixed."""
    hh = high > high.shift(window)
    hl = low > low.shift(window)
    lh = high < high.shift(window)
    ll = low < low.shift(window)
    bull = (hh & hl).astype(float)
    bear = (lh & ll).astype(float)
    return bull - bear
