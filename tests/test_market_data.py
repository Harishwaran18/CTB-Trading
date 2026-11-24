from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import market_data as md
from config import load_settings
from symbols import resolve

SETTINGS = load_settings()


@pytest.fixture(autouse=True)
def _clear_cache():
    md.clear_cache()
    yield
    md.clear_cache()


def _frame(*, n=3, freq="5min", end=None) -> pd.DataFrame:
    end = end or datetime(2024, 1, 1, tzinfo=timezone.utc)
    index = pd.date_range(end=end, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame(
        {
            "open": [1.0] * n,
            "high": [1.1] * n,
            "low": [0.9] * n,
            "close": [1.05] * n,
            "volume": [10.0] * n,
        },
        index=index,
    )


# --------------------------------------------------------------------------- #
# Provider parsers (real payload shapes, injected transport)
# --------------------------------------------------------------------------- #

def test_parse_yahoo(monkeypatch):
    payload = {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {"symbol": "EURUSD=X"},
                    "timestamp": [1700000300, 1700000000],
                    "indicators": {
                        "quote": [
                            {
                                "open": [2.0, 1.0],
                                "high": [2.1, 1.1],
                                "low": [1.9, 0.9],
                                "close": [2.05, 1.05],
                                "volume": [20, 10],
                            }
                        ]
                    },
                }
            ],
        }
    }
    monkeypatch.setattr(md, "_get_json", lambda *a, **k: payload)
    frame = md._fetch_yahoo(resolve("EUR/USD"), "5m", 10, 5.0)
    assert list(frame.columns) == md.COLUMNS
    assert frame.index.is_monotonic_increasing
    assert frame["close"].iloc[-1] == pytest.approx(2.05)


def test_parse_coinbase(monkeypatch):
    payload = {
        "candles": [
            {"start": "1700000300", "low": "1.9", "high": "2.1", "open": "2.0",
             "close": "2.05", "volume": "20"},
            {"start": "1700000000", "low": "0.9", "high": "1.1", "open": "1.0",
             "close": "1.05", "volume": "10"},
        ]
    }
    monkeypatch.setattr(md, "_get_json", lambda *a, **k: payload)
    frame = md._fetch_coinbase(resolve("BTC/USD"), "5m", 10, 5.0)
    assert frame.index.is_monotonic_increasing
    assert frame["close"].tolist() == pytest.approx([1.05, 2.05])


def test_parse_kraken(monkeypatch):
    payload = {
        "error": [],
        "result": {
            "XXBTZUSD": [
                [1700000000, "1.0", "1.1", "0.9", "1.05", "1.0", "10", 5],
                [1700000300, "2.0", "2.1", "1.9", "2.05", "2.0", "20", 6],
            ],
            "last": 1700000300,
        },
    }
    monkeypatch.setattr(md, "_get_json", lambda *a, **k: payload)
    frame = md._fetch_kraken(resolve("BTC/USD"), "5m", 10, 5.0)
    assert frame["close"].tolist() == pytest.approx([1.05, 2.05])


def test_parse_twelvedata(monkeypatch):
    payload = {
        "meta": {},
        "status": "ok",
        "values": [
            {"datetime": "2024-01-01 00:00:00", "open": "1.0", "high": "1.1",
             "low": "0.9", "close": "1.05", "volume": "10"},
            {"datetime": "2024-01-01 00:05:00", "open": "2.0", "high": "2.1",
             "low": "1.9", "close": "2.05", "volume": "20"},
        ],
    }
    monkeypatch.setattr(md, "_get_json", lambda *a, **k: payload)
    frame = md._fetch_twelvedata(resolve("EUR/USD"), "5m", 10, 5.0, "KEY")
    assert frame["close"].tolist() == pytest.approx([1.05, 2.05])


def test_twelvedata_error_payload_raises(monkeypatch):
    monkeypatch.setattr(
        md, "_get_json", lambda *a, **k: {"status": "error", "message": "bad key"}
    )
    with pytest.raises(md.MarketDataError):
        md._fetch_twelvedata(resolve("EUR/USD"), "5m", 10, 5.0, "BAD")


# --------------------------------------------------------------------------- #
# Fallback chain
# --------------------------------------------------------------------------- #

def test_fallback_chain_skips_failing_provider(monkeypatch):
    def boom(*args, **kwargs):
        raise md.MarketDataError("blocked")

    monkeypatch.setattr(md, "_fetch_coinbase", boom)
    monkeypatch.setattr(md, "_fetch_kraken", boom)
    monkeypatch.setattr(md, "_fetch_yahoo", lambda *a, **k: _frame(n=5))
    monkeypatch.setattr(md, "_fetch_twelvedata", boom)
    frame = md.get_candles("BTC/USD", "5m", 5, settings=SETTINGS)
    assert len(frame) == 5


def test_min_rows_skips_short_provider(monkeypatch):
    calls = {"yahoo": 0}

    monkeypatch.setattr(md, "_fetch_coinbase", lambda *a, **k: _frame(n=3))
    monkeypatch.setattr(md, "_fetch_kraken", lambda *a, **k: _frame(n=3))

    def yahoo(*a, **k):
        calls["yahoo"] += 1
        return _frame(n=500)

    monkeypatch.setattr(md, "_fetch_yahoo", yahoo)
    monkeypatch.setattr(md, "_fetch_twelvedata", lambda *a, **k: _frame(n=3))
    frame = md.get_candles("BTC/USD", "5m", 500, settings=SETTINGS, min_rows=100)
    assert len(frame) == 500
    assert calls["yahoo"] == 1


def test_all_providers_fail_raises(monkeypatch):
    def boom(*args, **kwargs):
        raise md.MarketDataError("down")

    for name in ("_fetch_coinbase", "_fetch_kraken", "_fetch_yahoo", "_fetch_twelvedata"):
        monkeypatch.setattr(md, name, boom)
    with pytest.raises(md.MarketDataError):
        md.get_candles("BTC/USD", "5m", 5, settings=SETTINGS)


# --------------------------------------------------------------------------- #
# Candle hygiene
# --------------------------------------------------------------------------- #

def test_only_closed_drops_forming_candle():
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    frame = _frame(n=3, end=now)  # last candle opens at `now`, closes at 12:05
    closed = md.only_closed(frame, "5m", now=now)
    assert len(closed) == 2
    assert closed.index[-1] < now


def test_is_stale_detects_old_data():
    now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    fresh = _frame(n=3, end=now - timedelta(minutes=5))
    old = _frame(n=3, end=now - timedelta(hours=3))
    assert md.is_stale(fresh, "5m", now=now) is False
    assert md.is_stale(old, "5m", now=now) is True
    assert md.is_stale(pd.DataFrame(), "5m", now=now) is True


def test_last_candle_time():
    frame = _frame(n=2)
    assert md.last_candle_time(frame) == int(frame.index[-1].timestamp())
    assert md.last_candle_time(pd.DataFrame()) == 0
