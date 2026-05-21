from decimal import Decimal

from crypto_trading.core.strategy import Strategy
from crypto_trading.core.types import OHLCV, OrderSide, OrderType, Signal


class MACrossoverStrategy(Strategy):
    """Moving average crossover — trend following.

    BUY/LONG when fast MA crosses above slow MA.
    SELL/SHORT when fast MA crosses below slow MA.
    """

    @property
    def _fast_period(self) -> int:
        return int(self.params.get("fast_period", 20))

    @property
    def _slow_period(self) -> int:
        return int(self.params.get("slow_period", 50))

    def _sma(self, closes: list[float], period: int) -> float | None:
        if len(closes) < period:
            return None
        return sum(closes[-period:]) / period

    async def on_bar(self, symbol: str, bar: OHLCV) -> Signal | None:
        closes = self._get_closes(symbol, self._slow_period + 1)
        if len(closes) < self._slow_period + 1:
            return None

        fast_curr = self._sma(closes, self._fast_period)
        fast_prev = self._sma(closes[:-1], self._fast_period)
        slow_curr = self._sma(closes, self._slow_period)
        slow_prev = self._sma(closes[:-1], self._slow_period)

        if None in (fast_curr, fast_prev, slow_curr, slow_prev):
            return None

        if fast_prev <= slow_prev and fast_curr > slow_curr:
            return Signal(
                symbol=symbol,
                side=OrderSide.BUY,
                amount=Decimal("0.01"),
                confidence=0.8,
                order_type=OrderType.MARKET,
            )

        if fast_prev >= slow_prev and fast_curr < slow_curr:
            return Signal(
                symbol=symbol,
                side=OrderSide.SELL,
                amount=Decimal("0.01"),
                confidence=0.8,
                order_type=OrderType.MARKET,
            )

        return None
