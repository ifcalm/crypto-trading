"""Moving average crossover — trend following.

BUY/LONG when fast MA crosses above slow MA.
SELL/SHORT when fast MA crosses below slow MA.
"""

from decimal import Decimal
from typing import Any

from crypto_trading.core.strategy import Strategy
from crypto_trading.core.types import OHLCV, OrderSide, OrderType, Signal


class MACrossoverStrategy(Strategy):
    """Moving average crossover — trend following.

    Uses incremental SMA: O(1) per bar instead of O(period).
    """

    def __init__(self, symbols: list[str], params: dict[str, Any] | None = None) -> None:
        super().__init__(symbols, params)
        self._sma_cache: dict[tuple[str, int], float] = {}  # (symbol, period) -> sum

    @property
    def _fast_period(self) -> int:
        return int(self.params.get("fast_period", 20))

    @property
    def _slow_period(self) -> int:
        return int(self.params.get("slow_period", 50))

    def _compute_sma_pair(
        self, symbol: str, closes: list[float], period: int
    ) -> tuple[float, float] | None:
        """Return (prev_sma, curr_sma) for a given period. Incremental O(1)."""
        if len(closes) < period + 1:
            return None

        key = (symbol, period)
        curr_sum = self._sma_cache.get(key, 0.0)

        if curr_sum == 0:
            curr_sum = sum(closes[-period:])
        else:
            curr_sum += closes[-1] - closes[-period - 1]

        self._sma_cache[key] = curr_sum
        prev_sum = curr_sum - closes[-1] + closes[-period - 1]

        return (prev_sum / period, curr_sum / period)

    async def on_bar(self, symbol: str, bar: OHLCV) -> Signal | None:
        closes = self._get_closes(symbol, self._slow_period + 1)
        if len(closes) < self._slow_period + 1:
            return None

        fast = self._compute_sma_pair(symbol, closes, self._fast_period)
        slow = self._compute_sma_pair(symbol, closes, self._slow_period)

        if fast is None or slow is None:
            return None

        fast_prev, fast_curr = fast
        slow_prev, slow_curr = slow

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
