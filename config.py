"""Configuration loaded from the environment (``.env`` supported)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from models import interval_seconds
from symbols import DEFAULT_SYMBOLS, resolve

load_dotenv()


def _str(key: str, default: str = "") -> str:
    value = os.getenv(key)
    return value.strip() if value is not None and value.strip() else default


def _int(key: str, default: int) -> int:
    try:
        return int(_str(key, str(default)))
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(_str(key, str(default)))
    except ValueError:
        return default


def _bool(key: str, default: bool) -> bool:
    return _str(key, str(default)).lower() in {"1", "true", "yes", "on"}


def _csv(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = _str(key)
    if not raw:
        return default
    items = tuple(part.strip().upper() for part in raw.split(",") if part.strip())
    return items or default


# Component weights for the confluence score. They sum to 100 so the
# normalised score is directly readable as a 0-100 confidence value.
DEFAULT_WEIGHTS: dict[str, float] = {
    "trend": 20.0,        # EMA 20/50/200 alignment
    "higher_tf": 15.0,    # higher timeframe bias agreement
    "macd": 12.0,         # MACD line vs signal + histogram slope
    "rsi": 12.0,          # RSI regime (momentum or reversion)
    "bollinger": 12.0,    # band position / mean reversion
    "stochastic": 9.0,    # stochastic cross
    "volume": 8.0,        # volume expansion in signal direction
    "structure": 7.0,     # swing structure (higher highs / lower lows)
    "pullback": 5.0,      # pullback entry within an established trend
}

STRATEGIES = ("adaptive", "trend", "reversion")


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    enable_telegram: bool = True
    twelvedata_api_key: str = ""

    symbols: tuple[str, ...] = DEFAULT_SYMBOLS
    base_interval: str = "5m"
    higher_interval: str = "15m"
    strategy: str = "adaptive"

    poll_seconds: int = 20
    min_confidence: float = 62.0
    dedup_ttl_minutes: int = 120
    cache_ttl_seconds: int = 15
    request_timeout: float = 15.0
    candle_limit: int = 400
    db_path: str = "ctb_state.db"
    log_level: str = "INFO"
    dry_run: bool = False

    account_balance: float = 1000.0
    risk_per_trade_pct: float = 1.0
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 2.5

    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    @property
    def base_seconds(self) -> int:
        return interval_seconds(self.base_interval)

    @property
    def higher_seconds(self) -> int:
        return interval_seconds(self.higher_interval)

    def instruments(self):
        return [resolve(name) for name in self.symbols]

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.enable_telegram and not self.dry_run:
            if not self.telegram_bot_token:
                problems.append("TELEGRAM_BOT_TOKEN is not set")
            if not self.telegram_chat_id:
                problems.append("TELEGRAM_CHAT_ID is not set")
        try:
            if self.higher_seconds <= self.base_seconds:
                problems.append(
                    "CTB_HIGHER_INTERVAL must be larger than CTB_BASE_INTERVAL"
                )
        except ValueError as exc:
            problems.append(str(exc))
        if not 0 < self.min_confidence <= 100:
            problems.append("CTB_MIN_CONFIDENCE must be between 1 and 100")
        if self.strategy not in STRATEGIES:
            problems.append(f"CTB_STRATEGY must be one of {STRATEGIES}")
        if self.poll_seconds < 1:
            problems.append("CTB_POLL_SECONDS must be >= 1")
        return problems


def load_settings(**overrides) -> Settings:
    settings = Settings(
        telegram_bot_token=_str("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_str("TELEGRAM_CHAT_ID"),
        enable_telegram=_bool("ENABLE_TELEGRAM", True),
        twelvedata_api_key=_str("TWELVEDATA_API_KEY"),
        symbols=_csv("CTB_SYMBOLS", DEFAULT_SYMBOLS),
        base_interval=_str("CTB_BASE_INTERVAL", "5m"),
        higher_interval=_str("CTB_HIGHER_INTERVAL", "15m"),
        strategy=_str("CTB_STRATEGY", "adaptive").lower(),
        poll_seconds=_int("CTB_POLL_SECONDS", 20),
        min_confidence=_float("CTB_MIN_CONFIDENCE", 62.0),
        dedup_ttl_minutes=_int("CTB_DEDUP_TTL_MINUTES", 120),
        cache_ttl_seconds=_int("CTB_CACHE_TTL_SECONDS", 15),
        request_timeout=_float("CTB_REQUEST_TIMEOUT", 15.0),
        db_path=_str("CTB_DB_PATH", "ctb_state.db"),
        log_level=_str("CTB_LOG_LEVEL", "INFO").upper(),
        dry_run=_bool("CTB_DRY_RUN", False),
        account_balance=_float("CTB_ACCOUNT_BALANCE", 1000.0),
        risk_per_trade_pct=_float("CTB_RISK_PER_TRADE_PCT", 1.0),
        sl_atr_mult=_float("CTB_SL_ATR_MULT", 1.5),
        tp_atr_mult=_float("CTB_TP_ATR_MULT", 2.5),
    )
    if overrides:
        from dataclasses import replace

        settings = replace(settings, **overrides)
    return settings
