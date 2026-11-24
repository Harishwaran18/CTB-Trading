"""Instrument registry.

Known instruments carry the exact ticker each provider expects. Unknown
symbols are inferred from their shape so new markets can be added from
``.env`` without touching code.
"""

from __future__ import annotations

from models import AssetClass, Symbol

_FOREX_CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "CNY", "HKD",
    "SGD", "SEK", "NOK", "MXN", "ZAR", "TRY", "INR", "BRL", "PLN", "DKK",
}

_CRYPTO_BASES = {
    "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LTC", "BCH", "DOT",
    "LINK", "MATIC", "TRX", "TON", "SHIB", "ATOM", "NEAR", "APT", "ARB", "OP",
}

_REGISTRY: dict[str, Symbol] = {}


def _crypto(name: str, base: str, kraken: str, decimals: int = 2) -> None:
    _REGISTRY[name] = Symbol(
        name=name,
        asset_class=AssetClass.CRYPTO,
        yahoo=f"{base}-USD",
        coinbase=f"{base}-USD",
        kraken=kraken,
        twelvedata=f"{base}/USD",
        price_decimals=decimals,
    )


def _forex(pair: str) -> None:
    _REGISTRY[pair] = Symbol(
        name=pair,
        asset_class=AssetClass.FOREX,
        yahoo=f"{pair.replace('/', '')}=X",
        twelvedata=pair,
        price_decimals=5,
    )


def _stock(ticker: str) -> None:
    _REGISTRY[ticker] = Symbol(
        name=ticker,
        asset_class=AssetClass.STOCK,
        yahoo=ticker,
        twelvedata=ticker,
        price_decimals=2,
    )


for _n, _b, _k, _d in (
    ("BTC/USD", "BTC", "XBTUSD", 2),
    ("ETH/USD", "ETH", "ETHUSD", 2),
    ("SOL/USD", "SOL", "SOLUSD", 3),
    ("XRP/USD", "XRP", "XRPUSD", 5),
    ("DOGE/USD", "DOGE", "XDGUSD", 6),
    ("ADA/USD", "ADA", "ADAUSD", 5),
    ("AVAX/USD", "AVAX", "AVAXUSD", 3),
    ("LTC/USD", "LTC", "LTCUSD", 2),
    ("LINK/USD", "LINK", "LINKUSD", 4),
    ("DOT/USD", "DOT", "DOTUSD", 4),
):
    _crypto(_n, _b, _k, _d)

for _p in (
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD", "USD/CHF",
    "NZD/USD", "EUR/GBP", "EUR/JPY", "GBP/JPY",
):
    _forex(_p)

for _t in (
    "AAPL", "MSFT", "TSLA", "NVDA", "AMZN", "GOOGL", "META", "AMD",
    "NFLX", "INTC", "SPY", "QQQ",
):
    _stock(_t)


def _infer(name: str) -> Symbol:
    """Best-effort symbol for tickers that are not in the registry."""
    key = name.upper().strip()
    if "/" in key:
        base, quote = key.split("/", 1)
        if base in _CRYPTO_BASES:
            return Symbol(
                name=key,
                asset_class=AssetClass.CRYPTO,
                yahoo=f"{base}-{quote}",
                coinbase=f"{base}-{quote}",
                twelvedata=key,
                price_decimals=4,
            )
        if base in _FOREX_CURRENCIES and quote in _FOREX_CURRENCIES:
            return Symbol(
                name=key,
                asset_class=AssetClass.FOREX,
                yahoo=f"{base}{quote}=X",
                twelvedata=key,
                price_decimals=5,
            )
        # Unknown slash pair: treat as crypto, Yahoo almost always resolves it.
        return Symbol(
            name=key,
            asset_class=AssetClass.CRYPTO,
            yahoo=f"{base}-{quote}",
            twelvedata=key,
            price_decimals=4,
        )
    if key in _CRYPTO_BASES:
        return Symbol(
            name=f"{key}/USD",
            asset_class=AssetClass.CRYPTO,
            yahoo=f"{key}-USD",
            coinbase=f"{key}-USD",
            twelvedata=f"{key}/USD",
            price_decimals=4,
        )
    return Symbol(
        name=key,
        asset_class=AssetClass.STOCK,
        yahoo=key,
        twelvedata=key,
        price_decimals=2,
    )


def resolve(name: str) -> Symbol:
    """Return the instrument definition for ``name``."""
    key = name.upper().strip()
    if key in _REGISTRY:
        return _REGISTRY[key]
    symbol = _infer(key)
    _REGISTRY[key] = symbol
    return symbol


def available() -> list[str]:
    return sorted(_REGISTRY)


DEFAULT_SYMBOLS = ("BTC/USD", "ETH/USD", "EUR/USD", "AAPL")
