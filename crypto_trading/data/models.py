from datetime import datetime

from sqlalchemy import DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TradeRecord(Base):
    __tablename__ = "trades"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    strategy: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    entry_price: Mapped[float] = mapped_column(Numeric(18, 8))
    exit_price: Mapped[float | None] = mapped_column(Numeric(18, 8), nullable=True)
    quantity: Mapped[float] = mapped_column(Numeric(18, 8))
    entry_time: Mapped[datetime] = mapped_column(DateTime)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Numeric(18, 8), nullable=True)
    pnl_pct: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    fee: Mapped[float] = mapped_column(Numeric(18, 8), default=0)
    exchange: Mapped[str] = mapped_column(String(32))
    market_type: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(16))  # backtest / paper / live
    leverage: Mapped[int] = mapped_column(Integer, default=1)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrderRecord(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    strategy: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(8))
    type: Mapped[str] = mapped_column(String(32))
    price: Mapped[float | None] = mapped_column(Numeric(18, 8), nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Numeric(18, 8), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(18, 8))
    filled: Mapped[float] = mapped_column(Numeric(18, 8), default=0)
    remaining: Mapped[float] = mapped_column(Numeric(18, 8), default=0)
    status: Mapped[str] = mapped_column(String(16), index=True)
    reduce_only: Mapped[bool] = mapped_column(default=False)
    cost: Mapped[float] = mapped_column(Numeric(18, 8), default=0)
    fee_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class OHLCVMeta(Base):
    __tablename__ = "ohlcv_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    earliest: Mapped[datetime] = mapped_column(DateTime)
    latest: Mapped[datetime] = mapped_column(DateTime)
    row_count: Mapped[int] = mapped_column(Integer)
    parquet_path: Mapped[str] = mapped_column(String(256))
