import asyncio
from decimal import Decimal
from enum import Enum

from crypto_trading.core.errors import ExchangeError, InsufficientBalanceError, OrderError
from crypto_trading.core.exchange import Exchange
from crypto_trading.core.logging import get_logger
from crypto_trading.core.types import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Portfolio,
    Position,
    PositionSide,
    Signal,
)
from crypto_trading.execution.broker import Broker

log = get_logger(__name__)


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
                    signal.order_type if signal.order_type != OrderType.MARKET else OrderType.MARKET
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
            self._apply_fill(signal, portfolio, order)
            return order

        elapsed = 0.0
        while elapsed < self.max_poll_time:
            await asyncio.sleep(self.poll_interval)
            elapsed += self.poll_interval
            try:
                updated = await self.exchange.fetch_order(
                    order.exchange_id or order.id, signal.symbol
                )
                if updated.status in (
                    OrderStatus.CLOSED,
                    OrderStatus.CANCELED,
                    OrderStatus.REJECTED,
                    OrderStatus.EXPIRED,
                ):
                    if updated.status == OrderStatus.CLOSED:
                        self._apply_fill(signal, portfolio, updated)
                    return updated
            except ExchangeError:
                continue

        return order

    def _apply_fill(self, signal: Signal, portfolio: Portfolio, order: Order) -> None:
        """Update portfolio to reflect a filled order."""
        fill_price = order.price or signal.price or Decimal("0")
        filled = order.filled if order.filled > 0 else signal.amount
        if fill_price == 0 or filled <= 0:
            return

        notional = filled * fill_price
        fee_cost = (
            Decimal(str(order.fee.get("cost", 0)))
            if order.fee
            else notional * self.exchange.trading_fee
        )
        margin = notional / Decimal(signal.leverage)
        target_side = PositionSide.LONG if signal.side == OrderSide.BUY else PositionSide.SHORT
        existing = portfolio.positions.get(signal.symbol)

        if signal.reduce_only:
            if existing is not None:
                portfolio.free_balance += existing.margin
                del portfolio.positions[signal.symbol]
                log.info(
                    "broker.position_closed",
                    symbol=signal.symbol,
                    price=float(fill_price),
                )
            return

        if existing is not None and existing.side != target_side:
            portfolio.free_balance += existing.margin
            del portfolio.positions[signal.symbol]

        if signal.symbol in portfolio.positions:
            return

        if margin + fee_cost > portfolio.free_balance:
            log.warning(
                "broker.insufficient_margin",
                symbol=signal.symbol,
                required=float(margin + fee_cost),
                available=float(portfolio.free_balance),
            )
            return

        portfolio.free_balance -= margin + fee_cost
        portfolio.positions[signal.symbol] = Position(
            symbol=signal.symbol,
            side=target_side,
            quantity=filled,
            entry_price=fill_price,
            mark_price=fill_price,
            leverage=signal.leverage,
            margin=margin,
        )
        log.info(
            "broker.position_opened",
            symbol=signal.symbol,
            side=target_side.value,
            quantity=float(filled),
            price=float(fill_price),
        )

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
