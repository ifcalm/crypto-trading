"""Repository layer for persisting trades and orders to SQLite."""

from __future__ import annotations

import json
from datetime import datetime

from crypto_trading.core.types import Order
from crypto_trading.data.database import get_session
from crypto_trading.data.models import OrderRecord, TradeRecord


async def save_order(
    order: Order,
    strategy: str,
    exchange: str,
    market_type: str,
    mode: str = "live",
) -> str:
    """Persist an Order to the database. Returns the record ID."""
    session = await get_session()
    async with session.begin():
        record = OrderRecord(
            id=order.id,
            exchange_order_id=order.exchange_id,
            strategy=strategy,
            symbol=order.symbol,
            side=order.side.value,
            type=order.type.value,
            price=float(order.price) if order.price else None,
            stop_price=float(order.stop_price) if order.stop_price else None,
            amount=float(order.amount),
            filled=float(order.filled),
            remaining=float(order.remaining),
            status=order.status.value,
            reduce_only=order.reduce_only,
            cost=float(order.cost),
            fee_json=json.dumps(order.fee) if order.fee else None,
            created_at=order.timestamp,
            updated_at=order.last_update,
        )
        session.add(record)
        return record.id


async def save_trade(
    trade_id: str,
    strategy: str,
    symbol: str,
    side: str,
    entry_price: float,
    quantity: float,
    entry_time: datetime,
    exit_price: float | None = None,
    exit_time: datetime | None = None,
    pnl: float | None = None,
    pnl_pct: float | None = None,
    fee: float = 0,
    exchange: str = "binance",
    market_type: str = "futures",
    mode: str = "backtest",
    leverage: int = 1,
    metadata: dict | None = None,
) -> str:
    """Persist a completed trade to the database. Returns the record ID."""
    session = await get_session()
    async with session.begin():
        record = TradeRecord(
            id=trade_id,
            strategy=strategy,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=quantity,
            entry_time=entry_time,
            exit_time=exit_time,
            pnl=pnl,
            pnl_pct=pnl_pct,
            fee=fee,
            exchange=exchange,
            market_type=market_type,
            mode=mode,
            leverage=leverage,
            metadata_json=json.dumps(metadata) if metadata else None,
        )
        session.add(record)
        return record.id
