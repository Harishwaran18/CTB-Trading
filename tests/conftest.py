import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models import Direction, Signal  # noqa: E402


def make_ohlc(
    n: int = 320,
    start: float = 100.0,
    drift: float = 0.0,
    noise: float = 0.05,
    spread: float = 0.15,
    volume: float = 1500.0,
    seed: int = 7,
    freq: str = "5min",
) -> pd.DataFrame:
    """Deterministic synthetic candles for engine tests."""
    rng = np.random.default_rng(seed)
    steps = drift + rng.normal(0.0, noise, n)
    close = start + np.cumsum(steps)
    open_ = np.empty(n)
    open_[0] = start
    open_[1:] = close[:-1]
    high = np.maximum(open_, close) + rng.uniform(0.0, spread, n)
    low = np.minimum(open_, close) - rng.uniform(0.0, spread, n)
    index = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, volume) * rng.uniform(0.8, 1.2, n),
        },
        index=index,
    )


@pytest.fixture
def uptrend() -> pd.DataFrame:
    return make_ohlc(drift=0.25, noise=0.04)


@pytest.fixture
def downtrend() -> pd.DataFrame:
    return make_ohlc(drift=-0.25, noise=0.04)


@pytest.fixture
def flat() -> pd.DataFrame:
    return make_ohlc(drift=0.0, noise=0.01, spread=0.02)


def make_signal(**overrides) -> Signal:
    base = dict(
        symbol="EUR/USD",
        asset_class="forex",
        interval="5m",
        direction=Direction.BUY,
        confidence=72.5,
        price=1.0950,
        candle_time=1_700_000_000,
        entry=1.0950,
        stop_loss=1.0940,
        take_profit=1.0967,
        risk_reward=1.67,
        atr=0.0007,
        score=72.5,
        regime="trending",
        reasons=["EMA stack bullish"],
        indicators={"rsi": 62.0},
        higher_direction=Direction.BUY,
        actionable=True,
    )
    base.update(overrides)
    return Signal(**base)
