"""Core data types shared across the bot."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60,
    "2m": 120,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "1d": 86400,
}


def interval_seconds(interval: str) -> int:
    try:
        return INTERVAL_SECONDS[interval]
    except KeyError:
        raise ValueError(
            f"Unsupported interval {interval!r}. Choose from {sorted(INTERVAL_SECONDS)}"
        ) from None


class AssetClass(str, Enum):
    CRYPTO = "crypto"
    FOREX = "forex"
    STOCK = "stock"


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    NEUTRAL = "NEUTRAL"

    @property
    def binary(self) -> str | None:
        """Direction name used by binary-option style platforms."""
        return {"BUY": "CALL", "SELL": "PUT"}.get(self.value)

    @property
    def sign(self) -> int:
        return {"BUY": 1, "SELL": -1}.get(self.value, 0)


class Regime(str, Enum):
    TRENDING = "trending"
    RANGING = "ranging"


@dataclass(frozen=True)
class Symbol:
    """An instrument and the per-provider ticker used to fetch it."""

    name: str
    asset_class: AssetClass
    yahoo: str
    coinbase: str | None = None
    kraken: str | None = None
    twelvedata: str | None = None
    price_decimals: int = 5

    @property
    def providers(self) -> tuple[str, ...]:
        """Ordered provider fallback chain for this asset class."""
        if self.asset_class is AssetClass.CRYPTO:
            return tuple(
                p
                for p in ("coinbase", "kraken", "twelvedata", "yahoo")
                if getattr(self, p)
            )
        # forex / stocks: TwelveData first when a key exists, Yahoo always works
        return tuple(p for p in ("twelvedata", "yahoo") if getattr(self, p))


@dataclass
class Signal:
    symbol: str
    asset_class: str
    interval: str
    direction: Direction
    confidence: float
    price: float
    candle_time: int
    entry: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    atr: float
    score: float
    regime: str
    reasons: list[str] = field(default_factory=list)
    indicators: dict[str, float] = field(default_factory=dict)
    higher_direction: Direction = Direction.NEUTRAL
    actionable: bool = False
    generated_at: float = field(default_factory=time.time)
    id: int | None = None

    @property
    def is_actionable(self) -> bool:
        return self.actionable and self.direction is not Direction.NEUTRAL

    @property
    def binary_direction(self) -> str | None:
        return self.direction.binary

    def dedup_key(self) -> tuple[str, str, int, str]:
        return (self.symbol, self.interval, self.candle_time, self.direction.value)


@dataclass
class TradeResult:
    """Outcome of a simulated trade, used by the backtester and paper trader."""

    symbol: str
    interval: str
    direction: Direction
    entry_time: int
    exit_time: int
    entry: float
    exit: float
    stop_loss: float
    take_profit: float
    outcome: str  # "win" | "loss" | "breakeven"
    r_multiple: float
    confidence: float = 0.0
    directional_hit: bool = False
    pnl: float = 0.0
