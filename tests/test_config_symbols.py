import pytest

from config import load_settings
from models import AssetClass
from symbols import available, resolve


def test_resolve_known_crypto():
    symbol = resolve("BTC/USD")
    assert symbol.asset_class is AssetClass.CRYPTO
    assert symbol.coinbase == "BTC-USD"
    assert symbol.yahoo == "BTC-USD"
    assert symbol.kraken == "XBTUSD"


def test_resolve_known_forex():
    symbol = resolve("EUR/USD")
    assert symbol.asset_class is AssetClass.FOREX
    assert symbol.yahoo == "EURUSD=X"
    assert symbol.twelvedata == "EUR/USD"


def test_resolve_known_stock():
    symbol = resolve("AAPL")
    assert symbol.asset_class is AssetClass.STOCK
    assert symbol.yahoo == "AAPL"


def test_infer_unknown_crypto_pair():
    symbol = resolve("LTC/USD")
    assert symbol.asset_class is AssetClass.CRYPTO
    assert symbol.coinbase == "LTC-USD"


def test_infer_unknown_forex_pair():
    symbol = resolve("USD/MXN")
    assert symbol.asset_class is AssetClass.FOREX
    assert symbol.yahoo == "USDMXN=X"


def test_provider_chain_order():
    assert resolve("BTC/USD").providers[0] == "coinbase"
    assert resolve("EUR/USD").providers[0] == "twelvedata"
    assert resolve("EUR/USD").providers[-1] == "yahoo"


def test_available_is_populated():
    names = available()
    assert "BTC/USD" in names
    assert "EUR/USD" in names
    assert "AAPL" in names


def test_settings_parse_symbols(monkeypatch):
    monkeypatch.setenv("CTB_SYMBOLS", "btc/usd, eth/usd , AAPL")
    monkeypatch.setenv("CTB_STRATEGY", "reversion")
    monkeypatch.setenv("CTB_MIN_CONFIDENCE", "70")
    settings = load_settings()
    assert settings.symbols == ("BTC/USD", "ETH/USD", "AAPL")
    assert settings.strategy == "reversion"
    assert settings.min_confidence == 70.0


def test_settings_defaults_are_valid(monkeypatch):
    monkeypatch.setenv("ENABLE_TELEGRAM", "false")
    settings = load_settings()
    assert settings.validate() == []


def test_settings_detect_bad_strategy(monkeypatch):
    monkeypatch.setenv("ENABLE_TELEGRAM", "false")
    monkeypatch.setenv("CTB_STRATEGY", "gambling")
    problems = load_settings().validate()
    assert any("CTB_STRATEGY" in problem for problem in problems)


def test_settings_detect_higher_interval_smaller(monkeypatch):
    monkeypatch.setenv("ENABLE_TELEGRAM", "false")
    monkeypatch.setenv("CTB_BASE_INTERVAL", "1h")
    monkeypatch.setenv("CTB_HIGHER_INTERVAL", "5m")
    problems = load_settings().validate()
    assert any("HIGHER_INTERVAL" in problem for problem in problems)


def test_settings_require_telegram_when_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_TELEGRAM", "true")
    monkeypatch.setenv("CTB_DRY_RUN", "false")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    problems = load_settings().validate()
    assert any("TELEGRAM_BOT_TOKEN" in problem for problem in problems)
