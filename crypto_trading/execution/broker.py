from abc import ABC, abstractmethod

from crypto_trading.core.types import Order, Portfolio, Signal


class Broker(ABC):
    @abstractmethod
    async def execute_signal(self, signal: Signal, portfolio: Portfolio) -> Order | None:
        """Execute a trading signal. Returns the resulting Order or None if skipped."""

    @abstractmethod
    async def cancel_open_orders(self, symbol: str) -> None:
        """Cancel all open orders for a symbol."""

    @abstractmethod
    async def close(self) -> None:
        """Clean up broker resources."""
