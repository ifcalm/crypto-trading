from decimal import Decimal

from crypto_trading.core.strategy import Strategy
from crypto_trading.core.types import OHLCV, OrderSide, OrderType, Signal


class RSIReversalStrategy(Strategy):
    """RSI mean reversion.

    BUY/LONG when RSI crosses below oversold level (oversold -> bounce).
    SELL/SHORT when RSI crosses above overbought level (overbought -> pullback).
    """

    @property
    def _period(self) -> int:
        return int(self.params.get("period", 14))

    @property
    def _oversold(self) -> float:
        return float(self.params.get("oversold", 30))

    @property
    def _overbought(self) -> float:
        return float(self.params.get("overbought", 70))

    def _rsi(self, closes: list[float]) -> float | None:
        period = self._period
        if len(closes) < period + 1:
            return None

        gains = 0.0
        losses = 0.0
        for i in range(-period, 0):
            diff = closes[i + 1] - closes[i]
            if diff > 0:
                gains += diff
            else:
                losses += abs(diff)

        avg_gain = gains / period
        avg_loss = losses / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    async def on_bar(self, symbol: str, bar: OHLCV) -> Signal | None:
        closes = self._get_closes(symbol, self._period + 2)
        if len(closes) < self._period + 2:
            return None

        rsi_curr = self._rsi(closes)
        rsi_prev = self._rsi(closes[:-1])

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
