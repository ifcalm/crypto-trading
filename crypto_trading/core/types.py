from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from uuid import uuid4


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MarketType(Enum):
    SPOT = "spot"
    FUTURES = "futures"


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    STOP_LOSS_LIMIT = "stop_loss_limit"
    TAKE_PROFIT_LIMIT = "take_profit_limit"


class OrderStatus(Enum):
    PENDING = "pending"
    OPEN = "open"
    CLOSED = "closed"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PositionSide(Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class OHLCV:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    symbol: str = ""
    metadata: dict | None = None


@dataclass
class Ticker:
    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: Decimal
    timestamp: datetime


@dataclass
class Balance:
    asset: str
    total: Decimal
    free: Decimal
    used: Decimal


@dataclass
class Order:
    symbol: str
    side: OrderSide
    type: OrderType
    amount: Decimal
    id: str = field(default_factory=lambda: uuid4().hex)
    exchange_id: str | None = None
    price: Decimal | None = None
    stop_price: Decimal | None = None
    filled: Decimal = Decimal("0")
    remaining: Decimal = Decimal("0")
    status: OrderStatus = OrderStatus.PENDING
    cost: Decimal = Decimal("0")
    fee: dict | None = None
    reduce_only: bool = False
    leverage: int = 1
    timestamp: datetime = field(default_factory=_utcnow)
    last_update: datetime = field(default_factory=_utcnow)
    metadata: dict | None = None


@dataclass
class Position:
    symbol: str
    side: PositionSide
    quantity: Decimal
    entry_price: Decimal
    mark_price: Decimal
    leverage: int
    margin: Decimal
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    liquidation_price: Decimal | None = None
    funding_fee: Decimal = Decimal("0")
    timestamp: datetime = field(default_factory=_utcnow)
    metadata: dict | None = None


@dataclass
class Signal:
    symbol: str
    side: OrderSide
    amount: Decimal
    confidence: float = 1.0
    reduce_only: bool = False
    order_type: OrderType = OrderType.MARKET
    price: Decimal | None = None
    stop_price: Decimal | None = None
    leverage: int = 1
    timestamp: datetime = field(default_factory=_utcnow)
    metadata: dict | None = None


@dataclass
class OrderBookLevel:
    price: Decimal
    quantity: Decimal

    @property
    def total(self) -> Decimal:
        return self.price * self.quantity


@dataclass
class OrderBookSnapshot:
    symbol: str
    timestamp: datetime
    bids: list[OrderBookLevel]  # sorted by price descending
    asks: list[OrderBookLevel]  # sorted by price ascending

    @property
    def best_bid(self) -> Decimal:
        return self.bids[0].price if self.bids else Decimal("0")

    @property
    def best_ask(self) -> Decimal:
        return self.asks[0].price if self.asks else Decimal("0")

    @property
    def mid_price(self) -> Decimal:
        return (self.best_bid + self.best_ask) / 2 if self.bids and self.asks else Decimal("0")

    @property
    def spread(self) -> Decimal:
        return self.best_ask - self.best_bid if self.bids and self.asks else Decimal("0")

    @property
    def spread_pct(self) -> Decimal:
        if self.mid_price == 0:
            return Decimal("0")
        return self.spread / self.mid_price

    @property
    def bid_volume(self) -> Decimal:
        return sum((level.quantity for level in self.bids), Decimal("0"))

    @property
    def ask_volume(self) -> Decimal:
        return sum((level.quantity for level in self.asks), Decimal("0"))

    @property
    def imbalance(self) -> Decimal:
        total = self.bid_volume + self.ask_volume
        if total == 0:
            return Decimal("0")
        return (self.bid_volume - self.ask_volume) / total


@dataclass
class Portfolio:
    """Portfolio aggregate root — all mutations go through methods.

    External code reads properties but MUST NOT set them directly.
    """

    total_equity: Decimal
    free_balance: Decimal
    positions: dict[str, Position] = field(default_factory=dict)
    open_orders: list[Order] = field(default_factory=list)
    peak_equity: Decimal = Decimal("0")
    current_drawdown: Decimal = Decimal("0")
    timestamp: datetime = field(default_factory=_utcnow)

    # ─── aggregate mutations ───────────────────────────────────────────

    def can_open(self, margin: Decimal, fee: Decimal) -> bool:
        """Check if portfolio has enough free balance for a new position."""
        return margin + fee <= self.free_balance

    def open_position(self, position: Position, margin: Decimal, fee: Decimal) -> None:
        """Open a new position, deduct margin + fee from free balance."""
        if position.symbol in self.positions:
            raise ValueError(f"Position already open for {position.symbol}")
        if not self.can_open(margin, fee):
            raise ValueError(f"Insufficient balance: need {margin + fee}, have {self.free_balance}")

        self.free_balance -= margin + fee
        self.positions[position.symbol] = position

    def close_position(self, symbol: str, price: Decimal, fee: Decimal) -> tuple[Position, Decimal]:
        """Close an existing position, release margin, return (closed_pos, pnl)."""
        pos = self.positions.get(symbol)
        if pos is None:
            raise ValueError(f"No position open for {symbol}")

        if pos.side == PositionSide.LONG:
            pnl = (price - pos.entry_price) * pos.quantity - fee
        else:
            pnl = (pos.entry_price - price) * pos.quantity - fee

        self.free_balance += pos.margin + pnl
        del self.positions[symbol]
        return pos, pnl

    def update_mark_prices(self, prices: dict[str, Decimal]) -> None:
        """Update unrealized PnL for open positions."""
        for symbol, price in prices.items():
            pos = self.positions.get(symbol)
            if pos is None:
                continue
            pos.mark_price = price
            if pos.side == PositionSide.LONG:
                pos.unrealized_pnl = (price - pos.entry_price) * pos.quantity
            else:
                pos.unrealized_pnl = (pos.entry_price - price) * pos.quantity

    def recompute_equity(self) -> Decimal:
        """Recompute total equity from free balance + positions (margin + unrealized)."""
        equity = self.free_balance
        for pos in self.positions.values():
            equity += pos.margin + pos.unrealized_pnl
        self.total_equity = equity
        self._update_drawdown()
        self.timestamp = _utcnow()
        return equity

    def _update_drawdown(self) -> None:
        if self.total_equity > self.peak_equity:
            self.peak_equity = self.total_equity
            self.current_drawdown = Decimal("0")
        elif self.peak_equity > 0:
            self.current_drawdown = (self.peak_equity - self.total_equity) / self.peak_equity

    @property
    def num_positions(self) -> int:
        return len(self.positions)
