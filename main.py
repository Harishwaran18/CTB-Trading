"""Command line entry point.

    python main.py run                      # live bot (default)
    python main.py once                     # one scan, then exit
    python main.py signal BTC/USD           # print a signal now
    python main.py backtest --symbol BTC/USD --interval 5m --bars 3000
    python main.py sweep --symbol EUR/USD --interval 15m
    python main.py stats                    # live paper-trade performance
    python main.py symbols                  # list known instruments
    python main.py chat-id                  # discover your Telegram chat id
    python main.py test-telegram            # verify Telegram connectivity
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from config import STRATEGIES, Settings, load_settings


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-12s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)


def _apply_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    from dataclasses import replace

    changes = {}
    if getattr(args, "interval", None):
        changes["base_interval"] = args.interval
    if getattr(args, "strategy", None):
        changes["strategy"] = args.strategy
    if getattr(args, "min_confidence", None) is not None:
        changes["min_confidence"] = args.min_confidence
    if getattr(args, "symbols", None):
        changes["symbols"] = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())
    return replace(settings, **changes) if changes else settings


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_run(args: argparse.Namespace) -> int:
    from bot import run_forever

    settings = _apply_overrides(load_settings(), args)
    asyncio.run(run_forever(settings))
    return 0


def cmd_once(args: argparse.Namespace) -> int:
    from bot import TradingBot

    settings = _apply_overrides(load_settings(), args)
    bot = TradingBot(settings)
    try:
        asyncio.run(bot.scan_once())
    finally:
        bot.close()
    print(json.dumps(bot.stats, indent=2))
    return 0


def cmd_signal(args: argparse.Namespace) -> int:
    from signal_engine import get_signal

    settings = _apply_overrides(load_settings(), args)
    names = [args.symbol] if args.symbol else list(settings.symbols)
    exit_code = 0
    for name in names:
        signal = get_signal(name, interval=args.interval, settings=settings)
        if signal is None:
            print(f"{name}: no signal (insufficient data or fetch failed)")
            exit_code = 1
            continue
        print("=" * 60)
        print(f"{signal.symbol}  {signal.asset_class}  {signal.interval}")
        print(f"  direction   : {signal.direction.value}"
              + (f"  ({signal.binary_direction})" if signal.binary_direction else ""))
        print(f"  confidence  : {signal.confidence:.1f}%")
        print(f"  score       : {signal.score:+.1f}")
        print(f"  regime      : {signal.regime}")
        print(f"  price       : {signal.price}")
        print(f"  entry       : {signal.entry}")
        print(f"  stop        : {signal.stop_loss}")
        print(f"  target      : {signal.take_profit}")
        print(f"  risk:reward : 1 : {signal.risk_reward}")
        print(f"  htf bias    : {signal.higher_direction.value}")
        print(f"  actionable  : {signal.is_actionable}")
        print(f"  reasons     : {'; '.join(signal.reasons)}")
    return exit_code


def cmd_backtest(args: argparse.Namespace) -> int:
    from backtest import run_backtest, sweep_confidence

    settings = _apply_overrides(load_settings(), args)
    if args.sweep:
        results = sweep_confidence(args.symbol, args.interval, args.bars, settings=settings)
        print(f"Confidence sweep for {args.symbol} {args.interval}")
        print(f"{'threshold':>9} {'trades':>7} {'win%':>6} {'avgR':>7} {'PF':>6}")
        for threshold, stats in results.items():
            if stats.get("trades"):
                print(
                    f"{threshold:>9.0f} {stats['trades']:>7} {stats['win_rate']:>6} "
                    f"{stats['avg_r']:>+7} {stats['profit_factor']:>6}"
                )
        return 0

    report = run_backtest(
        args.symbol, args.interval, args.bars, horizon=args.horizon, settings=settings
    )
    print(report.to_text())
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    from storage import StateStore

    settings = load_settings()
    store = StateStore(settings.db_path)
    try:
        overall = store.performance(args.symbol)
        print("Paper-trade performance" + (f" for {args.symbol}" if args.symbol else ""))
        print(json.dumps(overall, indent=2))
        print("\nRecent signals:")
        for row in store.recent_signals(args.limit):
            print(
                f"  {row['symbol']:9} {row['interval']:4} {row['direction']:4} "
                f"conf={row['confidence']:5.1f} price={row['price']}"
            )
    finally:
        store.close()
    return 0


def cmd_symbols(args: argparse.Namespace) -> int:
    from symbols import available

    print("Known instruments:")
    for name in available():
        print(f"  {name}")
    print("\nAny other ticker is inferred automatically (e.g. LTC/USD, AMD, GBP/JPY).")
    return 0


def cmd_chat_id(args: argparse.Namespace) -> int:
    from telegram import Bot

    settings = load_settings()
    if not settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN is not set in .env")
        return 1

    async def _fetch() -> None:
        bot = Bot(settings.telegram_bot_token)
        me = await bot.get_me()
        print(f"Bot: @{me.username}")
        updates = await bot.get_updates()
        if not updates:
            print("No updates yet. Send a message to your bot in Telegram, then rerun.")
            return
        seen = set()
        for update in updates:
            chat = update.effective_chat
            if chat and chat.id not in seen:
                seen.add(chat.id)
                print(f"chat_id={chat.id}  type={chat.type}  title={chat.title or chat.username or chat.first_name}")

    asyncio.run(_fetch())
    return 0


def cmd_test_telegram(args: argparse.Namespace) -> int:
    from telegram_sender import verify

    try:
        info = asyncio.run(verify(load_settings()))
        print(f"Connected as @{info['username']} ({info['name']}); message sent to {info['chat_id']}")
        return 0
    except Exception as exc:
        print(f"Telegram test failed: {exc}")
        return 1


def cmd_portal(args: argparse.Namespace) -> int:
    from portal import serve

    serve(args.host, args.port, reload=args.reload)
    return 0


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctb", description="CTB-Trading multi-market signal bot"
    )
    parser.add_argument("--log-level", default=None, help="DEBUG/INFO/WARNING/ERROR")
    sub = parser.add_subparsers(dest="command")

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--interval", help="base timeframe, e.g. 1m, 5m, 15m, 1h")
        p.add_argument("--strategy", choices=STRATEGIES, help="adaptive (default), trend, reversion")
        p.add_argument("--min-confidence", type=float, dest="min_confidence")
        p.add_argument("--symbols", help="comma separated symbols to override .env")

    run_p = sub.add_parser("run", help="start the live bot")
    add_common(run_p)
    run_p.set_defaults(func=cmd_run)

    once_p = sub.add_parser("once", help="run a single scan and exit")
    add_common(once_p)
    once_p.set_defaults(func=cmd_once)

    signal_p = sub.add_parser("signal", help="print a signal now")
    signal_p.add_argument("symbol", nargs="?")
    add_common(signal_p)
    signal_p.set_defaults(func=cmd_signal)

    bt_p = sub.add_parser("backtest", help="measure real accuracy on history")
    bt_p.add_argument("--symbol", default="BTC/USD")
    bt_p.add_argument("--interval", default="5m")
    bt_p.add_argument("--bars", type=int, default=3000)
    bt_p.add_argument("--horizon", type=int, default=None)
    bt_p.add_argument("--sweep", action="store_true", help="sweep confidence thresholds")
    bt_p.add_argument("--strategy", choices=STRATEGIES)
    bt_p.add_argument("--min-confidence", type=float, dest="min_confidence")
    bt_p.set_defaults(func=cmd_backtest)

    sweep_p = sub.add_parser("sweep", help="confidence threshold sweep")
    sweep_p.add_argument("--symbol", default="EUR/USD")
    sweep_p.add_argument("--interval", default="15m")
    sweep_p.add_argument("--bars", type=int, default=3000)
    sweep_p.add_argument("--strategy", choices=STRATEGIES)
    sweep_p.add_argument("--min-confidence", type=float, dest="min_confidence")
    sweep_p.set_defaults(func=cmd_backtest, sweep=True)

    stats_p = sub.add_parser("stats", help="live paper-trade performance")
    stats_p.add_argument("--symbol")
    stats_p.add_argument("--limit", type=int, default=10)
    stats_p.set_defaults(func=cmd_stats)

    sym_p = sub.add_parser("symbols", help="list known instruments")
    sym_p.set_defaults(func=cmd_symbols)

    chat_p = sub.add_parser("chat-id", help="discover Telegram chat id")
    chat_p.set_defaults(func=cmd_chat_id)

    tg_p = sub.add_parser("test-telegram", help="verify Telegram connectivity")
    tg_p.set_defaults(func=cmd_test_telegram)

    portal_p = sub.add_parser("portal", help="start the web dashboard")
    portal_p.add_argument("--host", default="0.0.0.0")
    portal_p.add_argument("--port", type=int, default=12000)
    portal_p.add_argument("--reload", action="store_true")
    portal_p.set_defaults(func=cmd_portal)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        args = parser.parse_args(["run"])
    _setup_logging(args.log_level or load_settings().log_level)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
