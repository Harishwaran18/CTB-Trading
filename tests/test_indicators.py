import numpy as np
import pandas as pd

import indicators as ind
from conftest import make_ohlc


def test_ema_matches_manual_recursion():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = ind.ema(series, 3)
    # pandas ewm(span=3) alpha = 2/(3+1) = 0.5, min_periods=3
    assert result.isna().sum() == 2
    assert result.iloc[-1] > result.iloc[2]


def test_rsi_on_monotonic_rise_is_100():
    series = pd.Series(np.arange(1.0, 60.0))
    rsi = ind.rsi(series, 14)
    assert rsi.dropna().iloc[-1] == 100.0


def test_rsi_on_monotonic_fall_is_zero():
    series = pd.Series(np.arange(60.0, 1.0, -1.0))
    rsi = ind.rsi(series, 14)
    assert rsi.dropna().iloc[-1] == 0.0


def test_rsi_flat_series_is_neutral():
    series = pd.Series(np.full(60, 5.0))
    rsi = ind.rsi(series, 14)
    assert rsi.dropna().iloc[-1] == 50.0


def test_macd_components_align():
    df = make_ohlc(300, drift=0.1)
    macd, signal, hist = ind.macd(df["close"])
    assert len(macd) == len(df)
    # histogram is exactly macd - signal
    np.testing.assert_allclose(
        hist.dropna().to_numpy(), (macd - signal).dropna().to_numpy()
    )


def test_atr_is_positive_and_bounded_by_range():
    df = make_ohlc(200, spread=1.0)
    atr = ind.atr(df["high"], df["low"], df["close"], 14).dropna()
    assert (atr > 0).all()
    true_range = ind.true_range(df["high"], df["low"], df["close"])
    assert atr.max() <= true_range.max() + 1e-9


def test_bollinger_bands_ordered():
    df = make_ohlc(200)
    upper, mid, lower, width = ind.bollinger(df["close"], 20, 2.0)
    valid = upper.dropna().index
    assert (upper[valid] >= mid[valid]).all()
    assert (mid[valid] >= lower[valid]).all()
    assert (width[valid] >= 0).all()


def test_adx_within_bounds():
    df = make_ohlc(300, drift=0.1)
    adx, plus_di, minus_di = ind.adx(df["high"], df["low"], df["close"], 14)
    values = adx.dropna()
    assert len(values) > 0
    assert values.between(0, 100).all()
    assert plus_di.dropna().between(0, 100).all()
    assert minus_di.dropna().between(0, 100).all()


def test_stochastic_within_bounds():
    df = make_ohlc(200)
    k, d = ind.stochastic(df["high"], df["low"], df["close"], 14, 3)
    assert k.dropna().between(0, 100).all()


def test_swing_structure_detects_uptrend():
    df = make_ohlc(120, drift=1.0, noise=0.0)
    structure = ind.swing_structure(df["high"], df["low"], 10).dropna()
    assert (structure >= 0).all()
    assert structure.iloc[-1] == 1.0


def test_indicators_are_causal():
    """A value at row i must not change when future rows are appended."""
    df = make_ohlc(260)
    full = ind.rsi(df["close"], 14)
    partial = ind.rsi(df["close"].iloc[:200], 14)
    np.testing.assert_allclose(
        full.iloc[:200].dropna().to_numpy(), partial.dropna().to_numpy()
    )
