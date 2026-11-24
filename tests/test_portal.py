from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import portal
from backtest import BacktestReport
from config import load_settings
from models import Direction, TradeResult


@pytest.fixture
def client():
    portal._scan_cache.update(ts=0.0, data=None)
    portal._market_cache.clear()
    return TestClient(portal.app)


def test_index_serves_dashboard(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Signal Desk" in res.text


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert "symbols" in body and body["symbols"]


def test_symbols(client):
    res = client.get("/api/symbols")
    assert res.status_code == 200
    assert "BTC/USD" in res.json()["symbols"]


def test_scan(client, monkeypatch):
    sample = [{
        "symbol": "EUR/USD", "asset_class": "forex", "interval": "5m",
        "direction": "BUY", "binary": "CALL", "confidence": 71.2, "score": 71.2,
        "regime": "trending", "price": 1.1, "entry": 1.1, "stop_loss": 1.09,
        "take_profit": 1.12, "risk_reward": 1.67, "atr": 0.001,
        "higher_direction": "BUY", "actionable": True, "reasons": ["x"],
        "indicators": {"rsi": 60.0}, "candle_time": 1700000000,
    }]
    monkeypatch.setattr(portal, "_scan", lambda: sample)
    res = client.get("/api/scan?refresh=true")
    assert res.status_code == 200
    body = res.json()
    assert body["signals"][0]["symbol"] == "EUR/USD"
    assert body["cached"] is False


def test_scan_uses_cache(client, monkeypatch):
    calls = {"n": 0}

    def fake_scan():
        calls["n"] += 1
        return [{"symbol": "BTC/USD", "direction": "SELL", "confidence": 10}]

    monkeypatch.setattr(portal, "_scan", fake_scan)
    first = client.get("/api/scan?refresh=true").json()
    second = client.get("/api/scan").json()
    assert calls["n"] == 1
    assert second["cached"] is True
    assert first["signals"] == second["signals"]


def test_performance(client, monkeypatch, tmp_path):
    monkeypatch.setattr(
        portal, "_settings", replace(load_settings(), db_path=str(tmp_path / "p.db"))
    )
    res = client.get("/api/performance")
    assert res.status_code == 200
    body = res.json()
    assert body["overall"]["trades"] == 0
    assert body["recent"] == []


def test_market(client, monkeypatch):
    payload = {
        "symbol": "EUR/USD", "asset_class": "forex", "interval": "5m",
        "candles": [{"t": 1, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}],
        "indicators": {"rsi": 55.0}, "signal": None,
    }
    monkeypatch.setattr(portal, "_market", lambda symbol, interval: payload)
    res = client.get("/api/market/EUR/USD?interval=5m")
    assert res.status_code == 200
    assert res.json()["symbol"] == "EUR/USD"


def test_market_rejects_bad_interval(client):
    res = client.get("/api/market/EUR/USD?interval=7m")
    assert res.status_code == 400


def _report() -> BacktestReport:
    trades = [
        TradeResult(
            symbol="EUR/USD", interval="15m", direction=Direction.BUY,
            entry_time=1, exit_time=2, entry=1.0, exit=1.1, stop_loss=0.99,
            take_profit=1.1, outcome="win", r_multiple=1.67, confidence=65.0,
            directional_hit=True,
        ),
        TradeResult(
            symbol="EUR/USD", interval="15m", direction=Direction.SELL,
            entry_time=3, exit_time=4, entry=1.0, exit=1.01, stop_loss=1.01,
            take_profit=0.9, outcome="loss", r_multiple=-1.0, confidence=75.0,
            directional_hit=False,
        ),
    ]
    return BacktestReport(symbol="EUR/USD", interval="15m", bars=500, horizon=12, trades=trades)


def test_backtest(client, monkeypatch):
    monkeypatch.setattr(portal, "run_backtest", lambda *a, **k: _report())
    res = client.get("/api/backtest?symbol=EUR/USD&interval=15m&bars=500")
    assert res.status_code == 200
    body = res.json()
    assert body["summary"]["overall"]["trades"] == 2
    assert body["directional"] == 50.0
    assert "BACKTEST" in body["text"]
    assert body["buckets"]


def test_backtest_rejects_bad_strategy(client):
    res = client.get("/api/backtest?strategy=gambling")
    assert res.status_code == 400


def test_json_safe_handles_nan():
    assert portal._json_safe(float("nan")) is None
    assert portal._json_safe(float("inf")) is None
    assert portal._json_safe({"a": [1.0, float("nan")]}) == {"a": [1.0, None]}
