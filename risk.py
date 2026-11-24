"""Risk management: stop-loss, take-profit and position sizing."""

from __future__ import annotations

from dataclasses import dataclass

from models import Direction


@dataclass(frozen=True)
class Levels:
    entry: float
    stop_loss: float
    take_profit: float
    risk_reward: float


def build_levels(
    direction: Direction,
    price: float,
    atr_value: float,
    sl_mult: float = 1.5,
    tp_mult: float = 2.5,
) -> Levels:
    """ATR-based bracket. Falls back to a small fixed fraction without ATR."""
    if atr_value <= 0 or price <= 0:
        distance = price * 0.001 if price > 0 else 0.0
    else:
        distance = atr_value

    risk = distance * sl_mult
    reward = distance * tp_mult
    if direction is Direction.BUY:
        stop, target = price - risk, price + reward
    elif direction is Direction.SELL:
        stop, target = price + risk, price - reward
    else:
        stop = target = price
        risk = 0.0

    rr = (reward / risk) if risk > 0 else 0.0
    return Levels(
        entry=round(price, 10),
        stop_loss=round(stop, 10),
        take_profit=round(target, 10),
        risk_reward=round(rr, 2),
    )


def position_size(
    balance: float,
    risk_pct: float,
    entry: float,
    stop_loss: float,
) -> float:
    """Units to trade so that a stop-out costs ``risk_pct`` of the balance."""
    risk_per_unit = abs(entry - stop_loss)
    if risk_per_unit <= 0 or balance <= 0 or risk_pct <= 0:
        return 0.0
    risk_amount = balance * (risk_pct / 100.0)
    return round(risk_amount / risk_per_unit, 8)


def pnl(
    direction: Direction,
    entry: float,
    exit_price: float,
    quantity: float,
) -> float:
    if direction is Direction.BUY:
        return (exit_price - entry) * quantity
    if direction is Direction.SELL:
        return (entry - exit_price) * quantity
    return 0.0
