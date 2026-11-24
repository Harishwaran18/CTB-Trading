"""Walk-forward backtester.

This is the honest counterpart to the live engine: it replays history bar by
bar, evaluates every bar with exactly the same :func:`signal_engine.evaluate`
function the bot uses live, and simulates the trade forward using only future
highs/lows. No look-ahead, no curve fitting to the score.

Run it to learn the *real* hit-rate for a market/timeframe instead of trusting
an advertised number:

    python main.py backtest --symbol BTC/USD --interval 5m --bars 3000
    python main.py backtest --symbol EUR/USD --interval 15m --bars 3000 --sweep
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config import Settings, load_settings
from market_data import get_candles
from models import Direction, TradeResult, interval_seconds
from signal_engine import WARMUP, compute_features, evaluate, next_higher, raw_score
from symbols import resolve

log = logging.getLogger("ctb.backtest")


@dataclass
class BacktestReport:
    symbol: str
    interval: str
    bars: int
    horizon: int
    trades: list[TradeResult] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    def _stats(self, trades: list[TradeResult]) -> dict:
        n = len(trades)
        if n == 0:
            return {"trades": 0}
        wins = sum(1 for t in trades if t.outcome == "win")
        losses = sum(1 for t in trades if t.outcome == "loss")
        flat = n - wins - losses
        r_values = np.array([t.r_multiple for t in trades], dtype=float)
        gross_win = float(r_values[r_values > 0].sum())
        gross_loss = float(-r_values[r_values < 0].sum())
        equity = np.cumsum(r_values)
        peak = np.maximum.accumulate(np.concatenate([[0.0], equity]))[1:]
        drawdown = float(np.max(peak - equity)) if n else 0.0
        return {
            "trades": n,
            "wins": wins,
            "losses": losses,
            "flat": flat,
            "win_rate": round(100.0 * wins / n, 1),
            "win_rate_excl_flat": round(100.0 * wins / max(wins + losses, 1), 1),
            "avg_r": round(float(r_values.mean()), 3),
            "total_r": round(float(r_values.sum()), 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
            "expectancy_r": round(float(r_values.mean()), 3),
            "max_drawdown_r": round(drawdown, 2),
        }

    def directional_accuracy(self) -> float:
        if not self.trades:
            return 0.0
        hits = sum(1 for trade in self.trades if trade.directional_hit)
        return 100.0 * hits / len(self.trades)

    def summary(self) -> dict:
        overall = self._stats(self.trades)
        overall["directional_accuracy"] = round(self.directional_accuracy(), 1)
        by_direction = {}
        for direction in (Direction.BUY, Direction.SELL):
            subset = [t for t in self.trades if t.direction is direction]
            if subset:
                by_direction[direction.value] = self._stats(subset)
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "bars": self.bars,
            "horizon": self.horizon,
            "overall": overall,
            "by_direction": by_direction,
        }

    def to_text(self) -> str:
        summary = self.summary()
        overall = summary["overall"]
        lines = [
            "=" * 62,
            f"BACKTEST  {self.symbol}  {self.interval}  ({self.bars} bars, "
            f"{self.horizon}-bar horizon)",
            "=" * 62,
        ]
        if overall.get("trades", 0) == 0:
            lines.append("No trades were triggered. Lower --min-confidence or add bars.")
            lines.append("=" * 62)
            return "\n".join(lines)

        lines.append(
            f"Trades        : {overall['trades']}  "
            f"(win {overall['wins']} / loss {overall['losses']} / flat {overall['flat']})"
        )
        lines.append(
            f"Win rate      : {overall['win_rate']}%  "
            f"(excl. flat {overall['win_rate_excl_flat']}%)"
        )
        lines.append(f"Avg R / trade : {overall['avg_r']}")
        lines.append(f"Total R       : {overall['total_r']}")
        lines.append(f"Profit factor : {overall['profit_factor']}")
        lines.append(f"Max drawdown  : {overall['max_drawdown_r']} R")
        lines.append(
            f"Directional hit rate (close after {self.horizon} bars) : "
            f"{self.directional_accuracy():.1f}%"
        )
        for name, stats in summary["by_direction"].items():
            lines.append(
                f"  {name:4} : {stats['trades']:4} trades, "
                f"{stats['win_rate']:5}% win, {stats['avg_r']:+.3f} avg R"
            )

        buckets = self._confidence_buckets()
        if buckets:
            lines.append("-" * 62)
            lines.append("Accuracy by confidence bucket (does confidence predict wins?)")
            for label, stats in buckets.items():
                if stats.get("trades"):
                    lines.append(
                        f"  {label:>8} : {stats['trades']:4} trades, "
                        f"{stats['win_rate']:5}% win, {stats['avg_r']:+.3f} avg R"
                    )
        lines.append("=" * 62)
        lines.append(
            "Past performance does not guarantee future results. "
            "A win rate near 50% with positive avg R is still a real edge."
        )
        return "\n".join(lines)

    def _confidence_buckets(self) -> dict[str, dict]:
        buckets: dict[str, list[TradeResult]] = {}
        for trade in self.trades:
            lower = int(trade.confidence // 10) * 10
            label = f"{lower}-{lower + 10}%"
            buckets.setdefault(label, []).append(trade)
        return {
            label: self._stats(subset)
            for label, subset in sorted(buckets.items(), key=lambda item: int(item[0].split("-")[0]))
        }


def _simulate(
    direction: Direction,
    entry: float,
    stop_loss: float,
    take_profit: float,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    start: int,
    horizon: int,
) -> tuple[str, int, float]:
    risk = abs(entry - stop_loss)
    last = min(start + horizon, len(closes))
    for index in range(start, last):
        high, low = highs[index], lows[index]
        if direction is Direction.BUY:
            hit_sl, hit_tp = low <= stop_loss, high >= take_profit
        else:
            hit_sl, hit_tp = high >= stop_loss, low <= take_profit
        if hit_sl:  # conservative: when both touch in one bar, count the stop
            return "loss", index, -1.0
        if hit_tp:
            return "win", index, (abs(take_profit - entry) / risk) if risk > 0 else 0.0
    if last <= start:
        return "breakeven", start, 0.0
    exit_index = last - 1
    exit_price = float(closes[exit_index])
    if risk <= 0:
        return "breakeven", exit_index, 0.0
    move = (exit_price - entry) if direction is Direction.BUY else (entry - exit_price)
    r_multiple = move / risk
    outcome = "win" if r_multiple > 0 else "loss" if r_multiple < 0 else "breakeven"
    return outcome, exit_index, r_multiple


def _align_htf_scores(
    base_index: pd.DatetimeIndex,
    htf_features: pd.DataFrame,
    htf_seconds: int,
    settings: Settings,
) -> np.ndarray:
    """As-of map each HTF score onto base bars, using only closed HTF candles."""
    scores = np.array(
        [
            raw_score(
                htf_features.iloc[i],
                htf_features.iloc[i - 1] if i > 0 else htf_features.iloc[i],
                settings,
            )
            for i in range(len(htf_features))
        ],
        dtype=float,
    )
    htf_close = htf_features.index + pd.Timedelta(seconds=htf_seconds)
    right = pd.DataFrame({"t": htf_close, "htf": scores}).sort_values("t")
    left = pd.DataFrame({"t": base_index})
    merged = pd.merge_asof(left.sort_values("t"), right, on="t", direction="backward")
    return merged["htf"].fillna(0.0).to_numpy()


def run_backtest(
    symbol: str,
    interval: str = "5m",
    bars: int = 2000,
    horizon: int | None = None,
    settings: Settings | None = None,
) -> BacktestReport:
    settings = settings or load_settings()
    instrument = resolve(symbol)
    htf_interval = next_higher(interval)
    horizon = horizon or 12

    base_raw = get_candles(
        instrument, interval, limit=bars + WARMUP + 50, settings=settings,
        min_rows=bars + WARMUP,
    )
    htf_raw = get_candles(
        instrument, htf_interval, limit=bars + WARMUP, settings=settings,
        min_rows=min(bars + WARMUP, 700),
    )
    base_raw = base_raw.tail(bars + WARMUP)
    htf_raw = htf_raw.tail(bars + WARMUP)

    base = compute_features(base_raw)
    htf = compute_features(htf_raw)
    if len(base) <= WARMUP or len(htf) < 3:
        raise ValueError(
            f"Not enough history for {symbol} {interval}: "
            f"{len(base)} base / {len(htf)} higher candles"
        )

    htf_scores = _align_htf_scores(base.index, htf, interval_seconds(htf_interval), settings)

    highs = base["high"].to_numpy(dtype=float)
    lows = base["low"].to_numpy(dtype=float)
    closes = base["close"].to_numpy(dtype=float)

    trades: list[TradeResult] = []
    last_direction: Direction | None = None

    for i in range(WARMUP, len(base) - 1):
        result = evaluate(base.iloc[i], base.iloc[i - 1], float(htf_scores[i]), settings)
        if not result["actionable"]:
            last_direction = None
            continue
        direction: Direction = result["direction"]
        if direction is last_direction:
            continue
        last_direction = direction

        entry = float(base.iloc[i + 1]["open"])  # execute on the next bar's open
        atr_value = float(base.iloc[i]["atr"])
        distance = atr_value if atr_value > 0 else entry * 0.001
        risk = distance * settings.sl_atr_mult
        reward = distance * settings.tp_atr_mult
        if direction is Direction.BUY:
            stop_loss, take_profit = entry - risk, entry + reward
        else:
            stop_loss, take_profit = entry + risk, entry - reward

        outcome, exit_index, r_multiple = _simulate(
            direction, entry, stop_loss, take_profit,
            highs, lows, closes, i + 1, horizon,
        )
        future_index = min(i + 1 + horizon, len(closes) - 1)
        future_close = float(closes[future_index])
        directional_hit = (
            future_close > entry if direction is Direction.BUY else future_close < entry
        )
        trades.append(
            TradeResult(
                symbol=instrument.name,
                interval=interval,
                direction=direction,
                entry_time=int(base.index[i + 1].timestamp()),
                exit_time=int(base.index[exit_index].timestamp()),
                entry=entry,
                exit=float(closes[exit_index]),
                stop_loss=stop_loss,
                take_profit=take_profit,
                outcome=outcome,
                r_multiple=round(r_multiple, 4),
                confidence=result["confidence"],
                directional_hit=directional_hit,
            )
        )

    return BacktestReport(
        symbol=instrument.name, interval=interval, bars=len(base), horizon=horizon, trades=trades
    )


def sweep_confidence(
    symbol: str,
    interval: str = "5m",
    bars: int = 2000,
    settings: Settings | None = None,
    thresholds: tuple[float, ...] = (50, 55, 60, 65, 70, 75, 80),
) -> dict[float, dict]:
    """Show how win rate / expectancy change with the confidence threshold."""
    settings = settings or load_settings()
    from dataclasses import replace

    report = run_backtest(symbol, interval, bars, settings=settings)
    results: dict[float, dict] = {}
    for threshold in thresholds:
        # Re-run scoring at each threshold without refetching is not stored,
        # so recompute from the cached candles the report already used.
        subset = replace(settings, min_confidence=threshold)
        try:
            sub_report = run_backtest(symbol, interval, bars, settings=subset)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("sweep %s failed: %s", threshold, exc)
            continue
        results[threshold] = sub_report.summary()["overall"]
    return results
