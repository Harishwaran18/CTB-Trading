# CTB-Trading

A real-time, multi-market signal and copy-trading engine for **crypto, forex and
stocks**. It pulls live candles, scores the market with a regime-adaptive
confluence model, publishes actionable signals to Telegram with entry, stop and
target levels, and tracks the outcome of every signal so accuracy is *measured*,
not assumed.

---

## Read this first: the honest accuracy statement

You asked for **90% accuracy**. Please read this carefully, because it is the
most important part of this project.

**No retail signal system reliably achieves 90% directional accuracy.** Anyone
who claims it is either:

- selling you something,
- reporting a curve-fitted backtest on one lucky symbol, or
- hiding losses behind their winners.

The real numbers, measured by the backtester in this repository on liquid
markets at 5m/15m, are:

| Metric | Realistic range |
|---|---|
| Directional hit rate (price higher/lower after N bars) | **50–58%** |
| Bracket win rate (TP vs SL, 1.67 R:R) | **38–52%** |
| Break-even win rate at 1.67 R:R | **37.5%** |
| Break-even win rate on binary options (80% payout) | **~56%** |

A 90% target is not reachable, and building a bot that pretends otherwise would
lose your money. What this project gives you instead is a *real edge you can
verify*: a positive-expectancy system on the markets where an edge exists, plus
the tooling to prove it on your own data before risking anything.

If a strategy shows a 50–58% hit rate with a positive average R and a profit
factor above 1.1, that is a genuine, tradable edge. It is not 90%. It is still
how professionals trade.

---

## What it does

- **Live multi-market data** with automatic provider fallback, so one blocked or
  rate-limited endpoint never stops the bot:
  - crypto: Coinbase → Kraken → TwelveData → Yahoo
  - forex: TwelveData → Yahoo
  - stocks: TwelveData → Yahoo
- **Regime-adaptive signal engine**: momentum and pullback in trends,
  mean-reversion at range extremes, with higher-timeframe confirmation.
- **No look-ahead**: signals are computed only from *closed* candles, so a
  published signal never changes afterwards.
- **Telegram delivery** with retries and HTML-safe formatting.
- **Deduplication and persistence** in SQLite, so the same candle never fires twice.
- **Live paper-trading** that records the outcome of each signal and reports
  real win rate / average R.
- **Walk-forward backtester** and confidence sweep so you can measure accuracy
  per symbol/timeframe yourself.
- **Graceful shutdown** and a fault-tolerant loop that survives any single
  symbol error.

---

## Project layout

```
main.py              CLI entry point (run / once / signal / backtest / sweep / stats)
bot.py               async real-time loop + paper-trade tracking
config.py            environment-driven settings and validation
models.py            dataclasses: Symbol, Signal, TradeResult, Direction, Regime
symbols.py            instrument registry (crypto/forex/stocks) + auto-inference
market_data.py       multi-provider data layer with cache, retries, fallback
indicators.py        vectorised, causal TA (EMA, RSI, MACD, ATR, BB, ADX, ...)
signal_engine.py     regime-adaptive confluence scorer (shared by live + backtest)
risk.py              ATR stop/target and position sizing
backtest.py          walk-forward backtester and confidence sweep
storage.py           SQLite state: signals, dedup, trades, performance
telegram_sender.py   Telegram delivery and message formatting
portal.py            FastAPI web portal + JSON API
static/              dashboard (HTML/CSS/JS, no external CDN)
tests/              84 unit/integration tests (no network required)
```

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # then edit .env
```

Minimum `.env`:

```ini
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=123456789
CTB_SYMBOLS=BTC/USD,ETH/USD,EUR/USD,AAPL
```

`TWELVEDATA_API_KEY` is optional. Without it the bot still works on every
market through Yahoo Finance.

Get your chat id:

```bash
python main.py chat-id      # send your bot a message first
python main.py test-telegram
```

---

## Usage

```bash
python main.py run                       # live bot (default)
python main.py once                      # one scan, then exit (great for cron)
python main.py signal BTC/USD            # print a signal right now
python main.py signal                    # all symbols from .env

# Measure real accuracy before you trust anything:
python main.py backtest --symbol BTC/USD --interval 5m --bars 3000
python main.py backtest --symbol EUR/USD --interval 15m --bars 3000 --sweep
python main.py backtest --symbol AAPL --interval 15m --strategy reversion

python main.py stats                     # live paper-trade win rate
python main.py symbols                   # list known instruments
python main.py portal --port 12000       # web dashboard
```

### Web portal

```bash
python main.py portal --host 0.0.0.0 --port 12000
# or: uvicorn portal:app --host 0.0.0.0 --port 12000
```

Open `http://localhost:12000` (or your public host URL). The dashboard is
served by the same engine as the bot:

- live market cards with confidence ring, direction, entry/stop/target and reasons
- click any card for a candlestick chart + full indicator snapshot
- recent signals table fed from `ctb_state.db`
- backtest lab: run any symbol/timeframe/strategy and see win rate, avg R,
  profit factor, directional hit rate and confidence buckets
- auto-refresh every 10s

Endpoints: `/api/health`, `/api/scan`, `/api/market/{symbol}`, `/api/performance`,
`/api/backtest`, `/api/symbols`. Run the bot (`python main.py run`) alongside the
portal so the signals table and performance stats keep filling in.

> The portal is read-only and exposes no credentials, but it has no login. Bind
> it to `127.0.0.1` unless you are behind a trusted network or proxy.

### Reading the backtest

```
Trades        : 87  (win 42 / loss 45 / flat 0)
Win rate      : 48.3%
Avg R / trade : +0.139
Profit factor : 1.34
Max drawdown  : 13.36 R
Directional hit rate (close after 12 bars) : 54.0%
```

- **Avg R / trade > 0** and **Profit factor > 1** means the strategy made money
  on that history.
- **Directional hit rate** is the "success rate" for binary-style / copy trades.
- The confidence-bucket table tells you whether a high score actually predicts
  wins. If it does not, the score is not calibrated and you should not trust it.
- Always check several symbols and both timeframes. If a setup only wins on one
  symbol, it is noise.

---

## How the score is built

Each closed candle produces independent votes in `[-1, +1]`:

| Component | Weight | Trending | Ranging |
|---|---|---|---|---|
| EMA 20/50/200 stack | 20 | ×1.4 | ×0.4 |
| Higher-timeframe bias | 15 | ×1.0 | ×0.6 |
| MACD (line/signal/hist slope) | 12 | ×1.3 | ×0.6 |
| RSI | 12 | momentum | mean-reversion |
| Bollinger position | 12 | ×0.6 | ×1.6 |
| Stochastic | 9 | ×0.6 | ×1.3 |
| Volume expansion | 8 | ×1.0 | ×0.8 |
| Swing structure | 7 | ×1.2 | ×0.5 |
| Trend pullback | 5 | ×1.3 | ×0.4 |

The weighted votes are normalised to `[-100, +100]`. `|score|` is the
confidence. A signal is only published when confidence clears
`CTB_MIN_CONFIDENCE` (default 62).

The **regime switch** (ADX ≥ 22 = trending) is the core of the edge: it stops
the bot buying the top of a range or fading a genuine trend.

### Strategy presets

`CTB_STRATEGY` (or `--strategy`) chooses:

- `adaptive` (default) – picks momentum or reversion per regime.
- `trend` – always momentum.
- `reversion` – always fade extremes.

Backtest each on your market and keep the one with positive expectancy.

---

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | – | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | – | Destination chat |
| `ENABLE_TELEGRAM` | true | Set false to silence |
| `TWELVEDATA_API_KEY` | – | Optional forex/stock candles |
| `CTB_SYMBOLS` | BTC/USD,ETH/USD,EUR/USD,AAPL | Universe |
| `CTB_BASE_INTERVAL` | 5m | Signal timeframe |
| `CTB_HIGHER_INTERVAL` | 15m | Higher timeframe (must be larger) |
| `CTB_STRATEGY` | adaptive | adaptive / trend / reversion |
| `CTB_POLL_SECONDS` | 20 | Loop cadence |
| `CTB_MIN_CONFIDENCE` | 62 | Publish threshold |
| `CTB_DEDUP_TTL_MINUTES` | 120 | Same-direction cool-down |
| `CTB_DB_PATH` | ctb_state.db | SQLite file |
| `CTB_DRY_RUN` | false | Log instead of sending |
| `CTB_ACCOUNT_BALANCE` | 1000 | For position sizing |
| `CTB_RISK_PER_TRADE_PCT` | 1.0 | Risk per trade |
| `CTB_SL_ATR_MULT` / `CTB_TP_ATR_MULT` | 1.5 / 2.5 | ATR bracket |

Any ticker not in the registry is inferred automatically (`LTC/USD`, `AMD`,
`USD/MXN`, ...).

---

## How accuracy is measured live

Every published signal is tracked in `ctb_state.db`. When its horizon elapses
the bot checks whether the stop or target was hit first (conservative: if both
touch in one bar, it counts as a loss) and stores the result. `python main.py
stats` shows the real win rate and average R of what you actually sent.

This is the number to trust. If it drops below break-even after 30+ trades,
raise `CTB_MIN_CONFIDENCE` or change `CTB_STRATEGY`.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No provider returned ...` | Provider geo-blocked; Yahoo fallback covers it. Check network. |
| `market closed or data stale` | Normal for stocks/forex outside session hours. |
| `HTTP 429` from TwelveData | Free plan limit. Remove the key or wait. |
| Telegram `401 Unauthorized` | Token revoked. Create a new bot. |
| No signals for hours | Confidence below threshold; that is by design. |

---

## Security

The original project had a **Telegram bot token and a TwelveData key hard-coded
in the source files**. They are now read from `.env` (git-ignored) only.

**Rotate both keys now** – anyone who saw the old files can use them. Do not
commit `.env`. If a key ever leaks, revoke it with @BotFather / TwelveData and
replace it in `.env`.

---



## Disclaimer

Educational software. Not financial advice. Trading carries substantial risk of
loss. Past performance does not predict future results. You are responsible for
every trade you take.
