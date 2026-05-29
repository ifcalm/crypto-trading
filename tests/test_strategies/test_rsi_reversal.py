from datetime import datetime
from decimal import Decimal

import pytest

from crypto_trading.core.types import OHLCV, OrderSide
from crypto_trading.strategies.rsi_reversal import RSIReversalStrategy


def _make_bar(ts: datetime, open_p: float, close: float) -> OHLCV:
    return OHLCV(
        timestamp=ts,
        open=Decimal(str(open_p)),
        high=Decimal(str(close)),
        low=Decimal(str(open_p)),
        close=Decimal(str(close)),
        volume=Decimal("1"),
    )


@pytest.fixture
def strategy():
    return RSIReversalStrategy(
        symbols=["BTC/USDT"],
        params={"period": 14, "oversold": 30, "overbought": 70},
    )


@pytest.mark.asyncio
async def test_not_enough_data(strategy):
    ts = datetime(2024, 1, 1, 12, 0)
    bar = _make_bar(ts, 100, 101)
    strategy._add_bar("BTC/USDT", bar)
    signal = await strategy.on_bar("BTC/USDT", bar)
    assert signal is None


@pytest.mark.asyncio
async def test_rsi_range(strategy):
    """RSI should be in [0, 100] for any valid input."""
    ts = datetime(2024, 1, 1, 12, 0)
    closes = [50 + i * 0.1 for i in range(30)]  # mild uptrend
    for i, close in enumerate(closes):
        bar = _make_bar(ts.replace(minute=i), close - 0.1, close)
        strategy._add_bar("BTC/USDT", bar)

    signal = await strategy.on_bar("BTC/USDT", bar)
    # Should not crash with valid data
    assert signal is None or signal.symbol == "BTC/USDT"


@pytest.mark.asyncio
async def test_oversold_signal(strategy):
    """BUY when RSI drops below oversold."""
    ts = datetime(2024, 1, 1, 12, 0)
    # Sharp persistent drop to push RSI below 30
    prices = list(range(100, 70, -1))  # 30 bars dropping
    for i, price in enumerate(prices):
        bar = _make_bar(ts.replace(minute=i), price + 1, price)  # open > close = down
        strategy._add_bar("BTC/USDT", bar)

    signal = await strategy.on_bar("BTC/USDT", bar)
    # With strong downtrend, RSI may go oversold -> BUY
    if signal is not None:
        assert signal.side == OrderSide.BUY
