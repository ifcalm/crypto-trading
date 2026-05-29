from decimal import Decimal

from crypto_trading.core.logging import get_logger
from crypto_trading.core.types import (
    Order,
    OrderSide,
    OrderStatus,
    Portfolio,
    Position,
    PositionSide,
    Signal,
)
from crypto_trading.execution.broker import Broker

log = get_logger(__name__)


class PaperBroker(Broker):
    """Simulated broker: fills at bar close + slippage."""

    def __init__(
        self,
        market_type: str = "futures",
        fee_rate: Decimal | None = None,
        slippage: Decimal = Decimal("0.0005"),
        leverage: int = 1,
    ):
        self.market_type = market_type
        self.fee_rate = fee_rate or (
            Decimal("0.0004") if market_type == "futures" else Decimal("0.001")
        )
        self.slippage = slippage
        self.leverage = leverage
        self._open_orders: dict[str, Order] = {}
        self._trades: list[tuple[Order, Order, Decimal]] = []

    async def execute_signal(self, signal: Signal, portfolio: Portfolio) -> Order | None:
        fill_price = signal.price or Decimal("0")
        if fill_price == 0 or signal.amount <= 0:
            log.debug("signal.skipped", symbol=signal.symbol, reason="no price or zero amount")
            return None

        if signal.side == OrderSide.BUY:
            fill_price = fill_price * (Decimal("1") + self.slippage)
        else:
            fill_price = fill_price * (Decimal("1") - self.slippage)

        notional = signal.amount * fill_price
        fee = notional * self.fee_rate
        existing = portfolio.positions.get(signal.symbol)
        target_side = PositionSide.LONG if signal.side == OrderSide.BUY else PositionSide.SHORT

        if signal.reduce_only:
            if existing is not None:
                return self._close(signal, portfolio, fill_price, fee)
            return None

        if existing is not None and existing.side != target_side:
            self._close(signal, portfolio, fill_price, fee)
            existing = None

        if existing is not None and existing.side == target_side:
            return None

        margin = notional / Decimal(self.leverage)
        if margin + fee > portfolio.free_balance:
            return None

        portfolio.free_balance -= margin + fee

        portfolio.positions[signal.symbol] = Position(
            symbol=signal.symbol,
            side=target_side,
            quantity=signal.amount,
            entry_price=fill_price,
            mark_price=fill_price,
            leverage=self.leverage,
            margin=margin,
        )

        order = Order(
            symbol=signal.symbol,
            side=signal.side,
            type=signal.order_type,
            amount=signal.amount,
            price=fill_price,
            filled=signal.amount,
            status=OrderStatus.CLOSED,
            cost=notional,
            fee={"cost": float(fee), "currency": "USDT"},
            leverage=self.leverage,
        )
        self._open_orders[signal.symbol] = order
        return order

    def _close(
        self, signal: Signal, portfolio: Portfolio, price: Decimal, fee: Decimal
    ) -> Order | None:
        pos = portfolio.positions.get(signal.symbol)
        if pos is None:
            return None

        if pos.side == PositionSide.LONG:
            pnl = (price - pos.entry_price) * pos.quantity - fee
        else:
            pnl = (pos.entry_price - price) * pos.quantity - fee

        portfolio.free_balance += pos.margin + pnl
        portfolio.total_equity += pnl

        order = Order(
            symbol=signal.symbol,
            side=signal.side,
            type=signal.order_type,
            amount=pos.quantity,
            price=price,
            filled=pos.quantity,
            status=OrderStatus.CLOSED,
            cost=pos.quantity * price,
            fee={"cost": float(fee), "currency": "USDT"},
            reduce_only=True,
            leverage=self.leverage,
        )

        entry_order = self._open_orders.pop(signal.symbol, None)
        if entry_order:
            self._trades.append((entry_order, order, float(pnl)))

        del portfolio.positions[signal.symbol]
        return order

    async def cancel_open_orders(self, symbol: str) -> None:
        pass

    async def close(self) -> None:
        pass

    @property
    def trades(self) -> list[tuple[Order, Order, float]]:
        return self._trades
