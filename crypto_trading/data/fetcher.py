import asyncio
from datetime import UTC, datetime, timedelta

from crypto_trading.core.exchange import Exchange
from crypto_trading.core.types import OHLCV
from crypto_trading.data.store import ParquetStore


class HistoricalDataFetcher:
    def __init__(self, exchange: Exchange, store: ParquetStore):
        self.exchange = exchange
        self.store = store

    async def fetch_and_store(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: datetime | None = None,
        until: datetime | None = None,
        batch_delay: float = 0.5,
    ) -> int:
        until = until or datetime.now(UTC).replace(tzinfo=None)
        since = since or until - timedelta(days=365)

        existing_range = self.store.get_date_range(symbol, timeframe)
        if existing_range:
            if existing_range[0] <= since and existing_range[1] >= until:
                return 0
            if existing_range[0] <= since:
                since = existing_range[1] + timedelta(seconds=1)

        all_bars: list[OHLCV] = []
        current = since
        limit = 1000

        while current < until:
            batch = await self.exchange.fetch_ohlcv(
                symbol=symbol,
                timeframe=timeframe,
                since=current,
                limit=limit,
            )

            if not batch:
                break

            batch = [b for b in batch if b.timestamp <= until]
            all_bars.extend(batch)

            if len(batch) < limit:
                break

            current = batch[-1].timestamp + timedelta(seconds=1)

            if batch_delay > 0:
                await asyncio.sleep(batch_delay)

        if all_bars:
            return self.store.write_ohlcv(symbol, timeframe, all_bars)

        return 0
