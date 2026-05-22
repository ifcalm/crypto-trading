from crypto_trading.core.types import Ticker
from crypto_trading.screener.filters import SymbolFilter


class SymbolScreener:
    """Chain multiple filters to produce a curated symbol list."""

    def __init__(self, filters: list[SymbolFilter] | None = None):
        self.filters = filters or []

    def add_filter(self, f: SymbolFilter) -> None:
        self.filters.append(f)

    def apply(self, tickers: list[Ticker]) -> list[str]:
        result = tickers
        for f in self.filters:
            result = f.apply(result)
        return sorted(t.symbol for t in result)
