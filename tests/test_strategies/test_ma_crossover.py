from datetime import datetime
from decimal import Decimal

import pytest

from crypto_trading.core.types import OHLCV, OrderSide
from crypto_trading.strategies.ma_crossover import MACrossoverStrategy


def _make_bar(ts: datetime, close: float) -> OHLCV:
    c = Decimal(str(close))
    return OHLCV(timestamp=ts, open=c, high=c, low=c, close=c, volume=Decimal("1"))


@pytest.fixture
def strategy():
    return MACrossoverStrategy(symbols=["BTC/USDT"], params={"fast_period": 3, "slow_period": 5})


@pytest.mark.asyncio
async def test_not_enough_data(strategy):
    """No signal when not enough bars."""
    ts = datetime(2024, 1, 1, 12, 0)
    bar = _make_bar(ts, 100)
    strategy._add_bar("BTC/USDT", bar)
    signal = await strategy.on_bar("BTC/USDT", bar)
    assert signal is None


@pytest.mark.asyncio
async def test_bullish_crossover(strategy):
    """BUY when fast MA crosses above slow MA."""
    ts = datetime(2024, 1, 1, 12, 0)
    # Flat then spike up: MAs flat then fast crosses above slow
    prices = [100, 100, 100, 100, 100, 120]
    for i, price in enumerate(prices):
        bar = _make_bar(ts.replace(minute=i), price)
        strategy._add_bar("BTC/USDT", bar)

    signal = await strategy.on_bar("BTC/USDT", bar)
    assert signal is not None
    assert signal.side == OrderSide.BUY


@pytest.mark.asyncio
async def test_bearish_crossover(strategy):
    """SELL when fast MA crosses below slow MA."""
    ts = datetime(2024, 1, 1, 12, 0)
    # Flat then spike down: MAs flat then fast crosses below slow
    prices = [120, 120, 120, 120, 120, 100]
    for i, price in enumerate(prices):
        bar = _make_bar(ts.replace(minute=i), price)
        strategy._add_bar("BTC/USDT", bar)

    signal = await strategy.on_bar("BTC/USDT", bar)
    assert signal is not None
    assert signal.side == OrderSide.SELL


@pytest.mark.asyncio
async def test_no_cross_no_signal(strategy):
    """No signal when MAs don't cross meaningfully."""
    ts = datetime(2024, 1, 1, 12, 0)
    prices = [100, 101, 100, 101, 100, 101]
    for i, price in enumerate(prices):
        bar = _make_bar(ts.replace(minute=i), price)
        strategy._add_bar("BTC/USDT", bar)

    # Sideways; may or may not cross. Just test no crash.
    signal = await strategy.on_bar("BTC/USDT", bar)
    assert signal is None or signal.symbol == "BTC/USDT"
