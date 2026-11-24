import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

import bot as botmod
from config import load_settings


def _frame(n=400, freq="5min", end=None, drift=0.3):
    end = end or datetime.now(timezone.utc).replace(second=0, microsecond=0)
    rng = np.random.default_rng(3)
    steps = drift + rng.normal(0, 0.03, n)
    close = 100 + np.cumsum(steps)
    open_ = np.concatenate([[100.0], close[:-1]])
    high = np.maximum(open_, close) + 0.1
    low = np.minimum(open_, close) - 0.1
    index = pd.date_range(end=end, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": np.full(n, 1000.0)},
        index=index,
    )


@pytest.fixture
def settings(tmp_path):
    return replace(
        load_settings(),
        db_path=str(tmp_path / "bot.db"),
        symbols=("EUR/USD",),
        base_interval="5m",
        higher_interval="15m",
        min_confidence=1.0,
        dry_run=False,
        enable_telegram=True,
        dedup_ttl_minutes=120,
    )


def _patch_data(monkeypatch, base_frame, htf_frame):
    def fake_get_candles(instrument, interval, limit=None, settings=None):
        return base_frame if interval == "5m" else htf_frame

    monkeypatch.setattr(botmod, "get_candles", fake_get_candles)


def test_cycle_emits_and_dedups(monkeypatch, settings):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    base = _frame(n=400, freq="5min", end=now - timedelta(minutes=5))
    htf = _frame(n=300, freq="15min", end=now - timedelta(minutes=15))
    _patch_data(monkeypatch, base, htf)

    sent = []

    async def fake_send(payload, s=None):
        sent.append(payload)
        return True

    monkeypatch.setattr(botmod, "send_signal", fake_send)

    bot = botmod.TradingBot(settings)
    try:
        asyncio.run(bot._cycle())
        assert bot.stats["signals"] == 1
        assert len(sent) == 1
        assert sent[0].direction.value in {"BUY", "SELL"}

        # Second cycle on the same closed candle must not re-emit.
        asyncio.run(bot._cycle())
        assert bot.stats["signals"] == 1
        assert len(store_records(bot)) == 1
    finally:
        bot.close()


def store_records(bot):
    return bot.store.recent_signals(10)


def test_stale_market_is_skipped(monkeypatch, settings):
    old = datetime.now(timezone.utc) - timedelta(days=2)
    base = _frame(n=400, freq="5min", end=old)
    htf = _frame(n=300, freq="15min", end=old)
    _patch_data(monkeypatch, base, htf)

    async def fake_send(payload, s=None):  # pragma: no cover - should not be called
        raise AssertionError("should not send for stale market")

    monkeypatch.setattr(botmod, "send_signal", fake_send)

    bot = botmod.TradingBot(settings)
    try:
        asyncio.run(bot._cycle())
        assert bot.stats["signals"] == 0
        assert bot.stats["evaluated"] == 0
    finally:
        bot.close()


def test_insufficient_history_is_skipped(monkeypatch, settings):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    base = _frame(n=50, freq="5min", end=now - timedelta(minutes=5))
    htf = _frame(n=50, freq="15min", end=now - timedelta(minutes=15))
    _patch_data(monkeypatch, base, htf)

    bot = botmod.TradingBot(settings)
    try:
        asyncio.run(bot._cycle())
        assert bot.stats["signals"] == 0
    finally:
        bot.close()


def test_dry_run_does_not_send(monkeypatch, settings):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    base = _frame(n=400, freq="5min", end=now - timedelta(minutes=5))
    htf = _frame(n=300, freq="15min", end=now - timedelta(minutes=15))
    _patch_data(monkeypatch, base, htf)

    called = []

    async def fake_send(payload, s=None):  # pragma: no cover
        called.append(payload)
        return True

    monkeypatch.setattr(botmod, "send_signal", fake_send)

    bot = botmod.TradingBot(replace(settings, dry_run=True))
    try:
        asyncio.run(bot._cycle())
        assert bot.stats["signals"] == 1
        assert called == []
    finally:
        bot.close()
