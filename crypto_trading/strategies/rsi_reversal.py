"""RSI mean reversion using Wilder's smoothing.

BUY/LONG when RSI crosses below oversold level (oversold -> bounce).
SELL/SHORT when RSI crosses above overbought level (overbought -> pullback).

Uses the standard Wilder EMA-based RSI with cached avg gain/loss for O(1) per bar.
"""

from decimal import Decimal
from typing import Any

from crypto_trading.core.strategy import Strategy
from crypto_trading.core.types import OHLCV, OrderSide, OrderType, Signal


class RSIReversalStrategy(Strategy):
    """RSI mean reversion.

    BUY/LONG when RSI crosses below oversold level (oversold -> bounce).
    SELL/SHORT when RSI crosses above overbought level (overbought -> pullback).
    """

    def __init__(self, symbols: list[str], params: dict[str, Any] | None = None) -> None:
        super().__init__(symbols, params)
        # Cached Wilder values: (symbol, period) -> (avg_gain, avg_loss, prev_close)
        self._rsi_cache: dict[tuple[str, int], tuple[float, float, Decimal | None]] = {}

    @property
    def _period(self) -> int:
        return int(self.params.get("period", 14))

    @property
    def _oversold(self) -> float:
        return float(self.params.get("oversold", 30))

    @property
    def _overbought(self) -> float:
        return float(self.params.get("overbought", 70))

    def _rsi(self, symbol: str, closes: list[Decimal]) -> float | None:
        """Wilder's RSI with incremental O(1) update per bar."""
        period = self._period
        if len(closes) < period + 1:
            return None

        key = (symbol, period)
        avg_gain, avg_loss, prev_close = self._rsi_cache.get(key, (0.0, 0.0, None))

        if prev_close is not None:
            # Incremental: update single bar (price diff as float for Wilder smoothing)
            diff = float(closes[-1] - prev_close)
            gain = diff if diff > 0 else 0.0
            loss = abs(diff) if diff < 0 else 0.0
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period
        else:
            # Initial: compute from first period bars
            gains = 0.0
            losses = 0.0
            for i in range(-period, 0):
                diff = float(closes[i + 1] - closes[i])
                if diff > 0:
                    gains += diff
                else:
                    losses += abs(diff)
            avg_gain = gains / period
            avg_loss = losses / period

        self._rsi_cache[key] = (avg_gain, avg_loss, closes[-1])

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    async def on_bar(self, symbol: str, bar: OHLCV) -> Signal | None:
        closes = self._get_closes(symbol, self._period + 2)
        if len(closes) < self._period + 2:
            return None

        rsi_curr = self._rsi(symbol, closes)
        rsi_prev = self._rsi(symbol, closes[:-1])

        if rsi_curr is None or rsi_prev is None:
            return None

        if rsi_prev >= self._oversold and rsi_curr < self._oversold:
            return Signal(
                symbol=symbol,
                side=OrderSide.BUY,
                amount=Decimal("0.01"),
                confidence=0.7,
                order_type=OrderType.MARKET,
            )

        if rsi_prev <= self._overbought and rsi_curr > self._overbought:
            return Signal(
                symbol=symbol,
                side=OrderSide.SELL,
                amount=Decimal("0.01"),
                confidence=0.7,
                order_type=OrderType.MARKET,
            )

        return None
