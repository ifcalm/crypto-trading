from datetime import datetime
from decimal import Decimal

import pytest

from crypto_trading.backtest.engine import BacktestEngine
from crypto_trading.core.strategy import Strategy
from crypto_trading.core.types import OHLCV, OrderSide, OrderType, Portfolio, Signal
from crypto_trading.data.store import ParquetStore
from crypto_trading.risk.manager import RiskManager


class OneShotStrategy(Strategy):
    def __init__(self):
        super().__init__(symbols=["BTC/USDT"])
        self.called = False

    async def on_bar(self, symbol: str, bar: OHLCV) -> Signal | None:
        if self.called:
            return None
        self.called = True
        return Signal(
            symbol=symbol,
            side=OrderSide.BUY,
            amount=Decimal("1"),
            order_type=OrderType.MARKET,
        )


class RecordingRiskManager(RiskManager):
    def __init__(self):
        super().__init__()
        self.seen_price: Decimal | None = None

    def check_signal(self, signal: Signal, portfolio: Portfolio) -> Signal | None:
        self.seen_price = signal.price
        return signal


def _bar(ts: datetime, close: str) -> OHLCV:
    price = Decimal(close)
    return OHLCV(
        timestamp=ts,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("1"),
    )


@pytest.mark.asyncio
async def test_backtest_sets_market_signal_price_before_risk(tmp_path):
    store = ParquetStore(base_dir=str(tmp_path))
    ts = datetime(2024, 1, 1, 0, 0)
    store.write_ohlcv("BTC/USDT", "1h", [_bar(ts, "100")])

    risk = RecordingRiskManager()
    engine = BacktestEngine(
        strategy=OneShotStrategy(),
        store=store,
        initial_capital=Decimal("1000"),
        risk_manager=risk,
    )

    await engine.run(
        symbols=["BTC/USDT"],
        timeframe="1h",
        start=ts,
        end=ts,
    )

    assert risk.seen_price == Decimal("100")
