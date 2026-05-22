from abc import ABC, abstractmethod
from collections import defaultdict

from crypto_trading.core.types import OHLCV, Signal, Ticker


class Strategy(ABC):
    def __init__(
        self,
        symbols: list[str],
        params: dict | None = None,
    ):
        self.symbols = symbols
        self.params = params or {}
        self._bar_history: dict[str, list[OHLCV]] = defaultdict(list)

    @abstractmethod
    async def on_bar(self, symbol: str, bar: OHLCV) -> Signal | None: ...

    async def on_start(self) -> None:
        pass

    async def on_stop(self) -> None:
        pass

    async def select_symbols(self, tickers: list[Ticker]) -> list[str]:
        """Override to filter symbols by strategy-specific criteria.
        Default: no further filtering — returns all ticker symbols."""
        return [t.symbol for t in tickers]

    def _add_bar(self, symbol: str, bar: OHLCV) -> None:
        self._bar_history[symbol].append(bar)

    def _get_bars(self, symbol: str, count: int) -> list[OHLCV]:
        return self._bar_history[symbol][-count:]

    def _get_closes(self, symbol: str, count: int) -> list[float]:
        return [float(b.close) for b in self._bar_history[symbol][-count:]]
