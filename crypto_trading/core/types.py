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
class Portfolio:
    total_equity: Decimal
    free_balance: Decimal
    positions: dict[str, Position] = field(default_factory=dict)
    open_orders: list[Order] = field(default_factory=list)
    peak_equity: Decimal = Decimal("0")
    current_drawdown: Decimal = Decimal("0")
    timestamp: datetime = field(default_factory=_utcnow)
