import pytest

from models import Direction
from risk import build_levels, pnl, position_size


def test_buy_levels_ordering_and_ratio():
    levels = build_levels(Direction.BUY, price=100.0, atr_value=1.0, sl_mult=1.5, tp_mult=2.5)
    assert levels.stop_loss < levels.entry < levels.take_profit
    assert levels.risk_reward == pytest.approx(round(2.5 / 1.5, 2))


def test_sell_levels_ordering():
    levels = build_levels(Direction.SELL, price=100.0, atr_value=1.0, sl_mult=1.5, tp_mult=2.5)
    assert levels.take_profit < levels.entry < levels.stop_loss


def test_neutral_levels_are_flat():
    levels = build_levels(Direction.NEUTRAL, price=50.0, atr_value=1.0)
    assert levels.stop_loss == levels.take_profit == levels.entry == 50.0
    assert levels.risk_reward == 0.0


def test_zero_atr_falls_back_to_fraction():
    levels = build_levels(Direction.BUY, price=100.0, atr_value=0.0)
    assert levels.stop_loss < levels.entry < levels.take_profit


def test_position_size_targets_risk_amount():
    # 1% of 1000 = 10 risk, 1 point stop -> 10 units
    assert position_size(1000, 1.0, 100.0, 99.0) == pytest.approx(10.0)


def test_position_size_guards_bad_input():
    assert position_size(0, 1.0, 100.0, 99.0) == 0.0
    assert position_size(1000, 0, 100.0, 99.0) == 0.0
    assert position_size(1000, 1.0, 100.0, 100.0) == 0.0


def test_pnl_long_and_short():
    assert pnl(Direction.BUY, 100.0, 101.0, 5) == pytest.approx(5.0)
    assert pnl(Direction.SELL, 100.0, 99.0, 5) == pytest.approx(5.0)
    assert pnl(Direction.NEUTRAL, 100.0, 110.0, 5) == 0.0
