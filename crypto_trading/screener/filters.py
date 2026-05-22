from abc import ABC, abstractmethod
from decimal import Decimal

from crypto_trading.core.types import Ticker


class SymbolFilter(ABC):
    @abstractmethod
    def apply(self, tickers: list[Ticker]) -> list[Ticker]: ...


class MinVolumeFilter(SymbolFilter):
    """Filter symbols by minimum 24h quote volume (USDT)."""

    def __init__(self, min_volume_usdt: float = 10_000_000):
        self.min_volume = Decimal(str(min_volume_usdt))

    def apply(self, tickers: list[Ticker]) -> list[Ticker]:
        return [t for t in tickers if t.volume >= self.min_volume]


class QuoteCurrencyFilter(SymbolFilter):
    """Only keep symbols with specific quote currency (e.g. USDT)."""

    def __init__(self, quote: str = "USDT"):
        self.quote = quote

    def apply(self, tickers: list[Ticker]) -> list[Ticker]:
        return [t for t in tickers if f"/{self.quote}" in t.symbol]


class MaxSymbolsFilter(SymbolFilter):
    """Keep top N symbols by volume."""

    def __init__(self, max_symbols: int = 20):
        self.max_symbols = max_symbols

    def apply(self, tickers: list[Ticker]) -> list[Ticker]:
        sorted_tickers = sorted(tickers, key=lambda t: t.volume, reverse=True)
        return sorted_tickers[: self.max_symbols]
