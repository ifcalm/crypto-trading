from datetime import UTC, datetime, timedelta
from decimal import Decimal

from crypto_trading.core.types import OHLCV


class StreamBuilder:
    """Aggregates incoming data into OHLCV bars for custom timeframes."""

    def __init__(self, timeframe: str = "1h"):
        self.timeframe = self._parse_timeframe(timeframe)
        self._buffers: dict[str, dict] = {}

    @staticmethod
    def _parse_timeframe(tf: str) -> timedelta:
        unit = tf[-1]
        value = int(tf[:-1])
        if unit == "m":
            return timedelta(minutes=value)
        elif unit == "h":
            return timedelta(hours=value)
        elif unit == "d":
            return timedelta(days=value)
        elif unit == "w":
            return timedelta(weeks=value)
        raise ValueError(f"Unknown timeframe: {tf}")

    def add_trade(
        self, symbol: str, price: Decimal, volume: Decimal, timestamp: datetime
    ) -> OHLCV | None:
        """Add a trade tick. Returns completed bar if a new candle started."""
        if symbol not in self._buffers:
            self._buffers[symbol] = {"start": timestamp, "bar": None}

        buf = self._buffers[symbol]
        candle_start = self._align_timestamp(timestamp)

        if buf["start"] != candle_start and buf["bar"] is not None:
            completed = buf["bar"]
            # Seed the new bar with this tick's data
            buf["bar"] = OHLCV(
                timestamp=candle_start,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume,
            )
            buf["start"] = candle_start
            return completed

        if buf["bar"] is None:
            buf["bar"] = OHLCV(
                timestamp=candle_start,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume,
            )
            buf["start"] = candle_start
        else:
            bar = buf["bar"]
            bar.high = max(bar.high, price)
            bar.low = min(bar.low, price)
            bar.close = price
            bar.volume += volume

        return None

    def _align_timestamp(self, ts: datetime) -> datetime:
        seconds = self.timeframe.total_seconds()
        epoch = ts.timestamp()
        aligned = int(epoch // seconds) * seconds
        return datetime.fromtimestamp(aligned, tz=UTC).replace(tzinfo=None)
