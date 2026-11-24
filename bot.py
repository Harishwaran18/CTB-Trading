"""Real-time orchestrator.

Runs a non-blocking loop that, for every configured symbol, waits for a candle
to close, evaluates it with the shared engine, de-duplicates, notifies Telegram
and tracks the outcome of each signal so live accuracy is measured rather than
assumed.

Signals are generated only from *closed* candles, so the score for a bar never
changes after it is published.
"""

from __future__ import annotations

import asyncio
import logging
import signal as signal_module
import time
from dataclasses import dataclass

import pandas as pd

from config import Settings, load_settings
from market_data import get_candles, is_stale, only_closed
from models import Direction, Signal, TradeResult, interval_seconds
from risk import position_size
from signal_engine import WARMUP, analyze
from storage import StateStore
from symbols import resolve
from telegram_sender import format_signal, send_signal

log = logging.getLogger("ctb.bot")


@dataclass
class _PendingTrade:
    signal: Signal
    entry: float
    stop_loss: float
    take_profit: float
    resolve_after: float  # epoch seconds
    horizon_bars: int


class TradingBot:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self.store = StateStore(self.settings.db_path)
        self._last_candle: dict[str, int] = {}
        self._pending: list[_PendingTrade] = []
        self._stop = asyncio.Event()
        self.stats = {"cycles": 0, "evaluated": 0, "signals": 0, "trades": 0, "errors": 0}

    # ------------------------------------------------------------------ #
    def request_stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self.store.close()

    # ------------------------------------------------------------------ #
    async def run(self) -> None:
        problems = self.settings.validate()
        for problem in problems:
            log.warning("config: %s", problem)

        log.info(
            "CTB-Trading starting | symbols=%s | base=%s htf=%s | strategy=%s "
            "| min_conf=%.0f | dry_run=%s",
            ",".join(self.settings.symbols),
            self.settings.base_interval,
            self.settings.higher_interval,
            self.settings.strategy,
            self.settings.min_confidence,
            self.settings.dry_run,
        )

        while not self._stop.is_set():
            started = time.time()
            await self._cycle()
            elapsed = time.time() - started
            delay = max(1.0, self.settings.poll_seconds - elapsed)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

        log.info("CTB-Trading stopped | %s", self.stats)

    async def scan_once(self) -> None:
        await self._cycle()

    async def _cycle(self) -> None:
        self.stats["cycles"] += 1
        for name in self.settings.symbols:
            if self._stop.is_set():
                break
            try:
                await self._process(name)
            except Exception as exc:  # keep the loop alive on any symbol error
                self.stats["errors"] += 1
                log.exception("%s: cycle error: %s", name, exc)

    # ------------------------------------------------------------------ #
    async def _process(self, name: str) -> None:
        instrument = resolve(name)
        interval = self.settings.base_interval
        htf_interval = self.settings.higher_interval

        raw = await asyncio.to_thread(get_candles, instrument, interval, None, self.settings)
        if is_stale(raw, interval):
            log.debug("%s: market closed or data stale", name)
            return

        base = only_closed(raw, interval)
        if len(base) < WARMUP:
            log.debug("%s: only %d closed candles, need %d", name, len(base), WARMUP)
            return

        candle_time = int(base.index[-1].timestamp())
        self._resolve_pending(name, base)

        if self._last_candle.get(name) == candle_time:
            return
        self._last_candle[name] = candle_time

        htf_raw = await asyncio.to_thread(get_candles, instrument, htf_interval, None, self.settings)
        htf = only_closed(htf_raw, htf_interval)

        signal = analyze(base, htf, instrument, interval, self.settings)
        if signal is None:
            return
        self.stats["evaluated"] += 1

        log.info(
            "%s | %s conf=%.0f score=%+.0f regime=%s price=%s",
            name, signal.direction.value, signal.confidence, signal.score,
            signal.regime, signal.price,
        )

        if not signal.is_actionable:
            return
        if self.store.is_duplicate(signal, self.settings.dedup_ttl_minutes):
            log.debug("%s: duplicate signal for candle %d skipped", name, candle_time)
            return

        self.store.record_signal(signal)
        self.stats["signals"] += 1
        self._track_trade(signal)

        if self.settings.dry_run:
            log.info("[dry-run]\n%s", format_signal(signal, self.settings))
            return
        await send_signal(signal, self.settings)

    # ------------------------------------------------------------------ #
    def _track_trade(self, signal: Signal) -> None:
        horizon = max(3, int(round(3600 / interval_seconds(signal.interval))))
        size = position_size(
            self.settings.account_balance, self.settings.risk_per_trade_pct,
            signal.entry, signal.stop_loss,
        )
        log.info(
            "%s %s | suggested size %.6f units | risk %.1f%% of %.2f",
            signal.symbol, signal.direction.value, size,
            self.settings.risk_per_trade_pct, self.settings.account_balance,
        )
        self._pending.append(
            _PendingTrade(
                signal=signal,
                entry=signal.entry,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                resolve_after=signal.candle_time + horizon * interval_seconds(signal.interval),
                horizon_bars=horizon,
            )
        )

    def _resolve_pending(self, name: str, base) -> None:
        """Settle any tracked trade whose horizon has fully elapsed."""
        if not self._pending:
            return
        now = int(base.index[-1].timestamp()) + interval_seconds(self.settings.base_interval)
        remaining: list[_PendingTrade] = []
        for trade in self._pending:
            if trade.signal.symbol != name or now < trade.resolve_after:
                remaining.append(trade)
                continue
            outcome, exit_price, r_multiple = self._settle(trade, base)
            result = TradeResult(
                symbol=trade.signal.symbol,
                interval=trade.signal.interval,
                direction=trade.signal.direction,
                entry_time=trade.signal.candle_time,
                exit_time=now,
                entry=trade.entry,
                exit=exit_price,
                stop_loss=trade.stop_loss,
                take_profit=trade.take_profit,
                outcome=outcome,
                r_multiple=round(r_multiple, 4),
                confidence=trade.signal.confidence,
                pnl=round(
                    position_size(
                        self.settings.account_balance,
                        self.settings.risk_per_trade_pct,
                        trade.entry, trade.stop_loss,
                    )
                    * r_multiple
                    * abs(trade.entry - trade.stop_loss),
                    2,
                ),
            )
            self.store.record_trade(result)
            self.stats["trades"] += 1
            log.info(
                "%s trade closed: %s (%.2f R)", name, outcome, r_multiple
            )
        self._pending = remaining

    def _settle(self, trade: _PendingTrade, base) -> tuple[str, float, float]:
        risk = abs(trade.entry - trade.stop_loss)
        direction = trade.signal.direction
        interval = interval_seconds(trade.signal.interval)
        signal_close = pd.Timestamp(trade.signal.candle_time + interval, unit="s", tz="UTC")
        after = base[base.index >= signal_close].head(trade.horizon_bars)
        if after.empty:
            return "breakeven", trade.entry, 0.0

        for _, row in after.iterrows():
            high, low = float(row["high"]), float(row["low"])
            if direction is Direction.BUY:
                if low <= trade.stop_loss:
                    return "loss", trade.stop_loss, -1.0
                if high >= trade.take_profit:
                    return "win", trade.take_profit, self._reward_r(trade, risk)
            else:
                if high >= trade.stop_loss:
                    return "loss", trade.stop_loss, -1.0
                if low <= trade.take_profit:
                    return "win", trade.take_profit, self._reward_r(trade, risk)

        exit_price = float(after["close"].iloc[-1])
        move = (
            (exit_price - trade.entry)
            if direction is Direction.BUY
            else (trade.entry - exit_price)
        )
        r_multiple = move / risk if risk else 0.0
        outcome = "win" if r_multiple > 0 else "loss" if r_multiple < 0 else "breakeven"
        return outcome, exit_price, r_multiple

    @staticmethod
    def _reward_r(trade: _PendingTrade, risk: float) -> float:
        return (abs(trade.take_profit - trade.entry) / risk) if risk else 0.0


async def run_forever(settings: Settings | None = None) -> None:
    bot = TradingBot(settings)
    loop = asyncio.get_running_loop()
    for sig in (signal_module.SIGINT, signal_module.SIGTERM):
        try:
            loop.add_signal_handler(sig, bot.request_stop)
        except NotImplementedError:  # pragma: no cover - Windows
            pass
    try:
        await bot.run()
    finally:
        bot.close()
