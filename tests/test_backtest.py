import numpy as np
import pytest

from backtest import BacktestReport, _simulate
from models import Direction


def _arrays(prices):
    arr = np.array(prices, dtype=float)
    return arr, arr, arr


def test_long_hits_target_is_win():
    highs = np.array([101.0, 105.0])
    lows = np.array([99.0, 100.0])
    closes = np.array([101.0, 104.0])
    outcome, index, r = _simulate(Direction.BUY, 100.0, 98.0, 104.0, highs, lows, closes, 0, 5)
    assert outcome == "win"
    assert index == 1
    assert r == 2.0  # 4 reward / 2 risk


def test_long_hits_stop_is_loss():
    highs = np.array([100.5, 100.0])
    lows = np.array([99.0, 97.0])
    closes = np.array([99.5, 97.5])
    outcome, _, r = _simulate(Direction.BUY, 100.0, 98.0, 104.0, highs, lows, closes, 0, 5)
    assert outcome == "loss"
    assert r == -1.0


def test_short_hits_target_is_win():
    highs = np.array([101.0, 100.0])
    lows = np.array([99.0, 96.0])
    closes = np.array([100.0, 96.5])
    outcome, _, r = _simulate(Direction.SELL, 100.0, 102.0, 96.0, highs, lows, closes, 0, 5)
    assert outcome == "win"
    assert r == 2.0


def test_short_hits_stop_is_loss():
    highs = np.array([101.0, 103.0])
    lows = np.array([99.5, 101.0])
    closes = np.array([100.5, 102.5])
    outcome, _, _ = _simulate(Direction.SELL, 100.0, 102.0, 96.0, highs, lows, closes, 0, 5)
    assert outcome == "loss"


def test_same_bar_stop_and_target_counts_stop():
    # Conservative: when both levels are touched in one bar, assume the stop.
    highs = np.array([105.0])
    lows = np.array([97.0])
    closes = np.array([101.0])
    outcome, _, r = _simulate(Direction.BUY, 100.0, 98.0, 104.0, highs, lows, closes, 0, 1)
    assert outcome == "loss"
    assert r == -1.0


def test_timeout_exits_at_close():
    highs = np.array([100.5, 100.5])
    lows = np.array([99.5, 99.5])
    closes = np.array([100.2, 100.6])
    outcome, index, r = _simulate(Direction.BUY, 100.0, 98.0, 110.0, highs, lows, closes, 0, 2)
    assert outcome == "win"
    assert index == 1
    assert r == pytest.approx(0.3)


def test_report_empty_is_safe():
    report = BacktestReport(symbol="X", interval="5m", bars=0, horizon=12, trades=[])
    assert report.directional_accuracy() == 0.0
    assert "No trades" in report.to_text()
