from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal

from crypto_trading.core.types import OHLCV, Balance, Order, OrderSide, OrderType, Position, Ticker


class Exchange(ABC):
    """Abstract exchange interface. CEX and DEX implementations share this contract."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def market_type(self) -> str: ...

    @property
    @abstractmethod
    def trading_fee(self) -> Decimal: ...

    @abstractmethod
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[OHLCV]: ...

    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> Ticker: ...

    @abstractmethod
    async def fetch_balance(self) -> dict[str, Balance]: ...

    @abstractmethod
    async def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        amount: Decimal,
        price: Decimal | None = None,
        stop_price: Decimal | None = None,
        reduce_only: bool = False,
        params: dict | None = None,
    ) -> Order: ...

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> bool: ...

    @abstractmethod
    async def fetch_order(self, order_id: str, symbol: str) -> Order: ...

    @abstractmethod
    async def fetch_open_orders(self, symbol: str | None = None) -> list[Order]: ...

    @abstractmethod
    async def fetch_my_trades(
        self,
        symbol: str,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[Order]: ...

    @abstractmethod
    async def fetch_positions(self) -> list[Position]: ...

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> None: ...

    @abstractmethod
    async def set_margin_mode(self, symbol: str, mode: str) -> None: ...

    @abstractmethod
    async def fetch_funding_rate(self, symbol: str) -> Decimal: ...

    @abstractmethod
    async def close(self) -> None: ...
