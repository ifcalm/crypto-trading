import asyncio
from enum import Enum

from crypto_trading.core.errors import ExchangeError, InsufficientBalanceError, OrderError
from crypto_trading.core.exchange import Exchange
from crypto_trading.core.types import (
    Order,
    OrderStatus,
    OrderType,
    Portfolio,
    Signal,
)
from crypto_trading.execution.broker import Broker


class LiveMode(Enum):
    LIVE = "live"
    TESTNET = "testnet"


class LiveBroker(Broker):
    """Real broker: places orders on the exchange, handles fills and errors."""

    def __init__(
        self,
        exchange: Exchange,
        mode: str = "testnet",
        max_retries: int = 3,
        poll_interval: float = 0.5,
        max_poll_time: float = 30.0,
    ):
        self.exchange = exchange
        self.mode = LiveMode(mode)
        self.max_retries = max_retries
        self.poll_interval = poll_interval
        self.max_poll_time = max_poll_time

    async def execute_signal(self, signal: Signal, portfolio: Portfolio) -> Order | None:
        if signal.amount <= 0:
            return None

        for attempt in range(self.max_retries):
            try:
                return await self._place_and_wait(signal, portfolio)
            except InsufficientBalanceError:
                return None
            except OrderError:
                if attempt == self.max_retries - 1:
                    return None
                await asyncio.sleep(2**attempt)

        return None

    async def _place_and_wait(self, signal: Signal, portfolio: Portfolio) -> Order | None:
        if self.exchange.market_type == "futures":
            try:
                await self.exchange.set_leverage(signal.symbol, signal.leverage)
            except ExchangeError:
                pass

        try:
            order = await self.exchange.create_order(
                symbol=signal.symbol,
                side=signal.side,
                order_type=(
                    signal.order_type if signal.order_type != OrderType.MARKET
                    else OrderType.MARKET
                ),
                amount=signal.amount,
                price=signal.price,
                stop_price=signal.stop_price,
                reduce_only=signal.reduce_only,
            )
        except ExchangeError as e:
            if "insufficient" in str(e).lower():
                raise InsufficientBalanceError(str(e)) from e
            raise OrderError(str(e)) from e

        if order.status == OrderStatus.CLOSED:
            return order

        elapsed = 0.0
        while elapsed < self.max_poll_time:
            await asyncio.sleep(self.poll_interval)
            elapsed += self.poll_interval
            try:
                updated = await self.exchange.fetch_order(
                    order.exchange_id or order.id, signal.symbol
                )
                if updated.status in (OrderStatus.CLOSED, OrderStatus.CANCELED,
                                      OrderStatus.REJECTED, OrderStatus.EXPIRED):
                    return updated
            except ExchangeError:
                continue

        return order

    async def cancel_open_orders(self, symbol: str) -> None:
        try:
            open_orders = await self.exchange.fetch_open_orders(symbol)
            for order in open_orders:
                oid = order.exchange_id or order.id
                await self.exchange.cancel_order(oid, symbol)
        except ExchangeError:
            pass

    async def close(self) -> None:
        await self.exchange.close()
