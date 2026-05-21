import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal

import websockets

from crypto_trading.core.types import OHLCV

# Binance WebSocket base URLs
WS_BASE = "wss://fapi.binance.com/ws"       # Futures
WS_BASE_SPOT = "wss://stream.binance.com:9443/ws"  # Spot


class BinanceWebSocket:
    def __init__(
        self,
        symbols: list[str],
        timeframes: list[str],
        market_type: str = "futures",
        proxy: str | None = None,
    ):
        self.symbols = [s.lower().replace("/", "") for s in symbols]
        self.timeframes = timeframes
        self.market_type = market_type
        self._ws_url = WS_BASE_SPOT if market_type == "spot" else WS_BASE
        self._proxy = proxy
        self._queue: asyncio.Queue[OHLCV] = asyncio.Queue()
        self._running = False
        self._ws: websockets.WebSocketClientProtocol | None = None

    async def stream(self):
        """Async generator yielding completed OHLCV bars."""
        self._running = True
        task = asyncio.create_task(self._connect())

        try:
            while self._running:
                try:
                    bar = await asyncio.wait_for(self._queue.get(), timeout=30)
                    yield bar
                except TimeoutError:
                    continue
        finally:
            self._running = False
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _connect(self) -> None:
        streams = []
        for symbol in self.symbols:
            for tf in self.timeframes:
                streams.append(f"{symbol}@kline_{tf}")

        url = f"{self._ws_url}/{'/'.join(streams)}"

        kwargs: dict = {}
        if self._proxy:
            kwargs["proxy"] = self._proxy

        while self._running:
            try:
                async with websockets.connect(url, **kwargs) as ws:  # type: ignore[attr-defined]
                    self._ws = ws
                    async for message in ws:
                        await self._handle_message(message)
            except asyncio.CancelledError:
                break
            except Exception:
                if self._running:
                    await asyncio.sleep(5)

    async def _handle_message(self, message: str | bytes) -> None:
        data = json.loads(message)
        if "e" in data and data["e"] == "kline":
            kline = data["k"]
            if kline["x"]:  # is closed
                raw_symbol = kline["s"]
                if raw_symbol.endswith("USDT"):
                    symbol = f"{raw_symbol[:-4]}/{raw_symbol[-4:]}"
                else:
                    symbol = raw_symbol
                bar = OHLCV(
                    timestamp=datetime.fromtimestamp(kline["t"] / 1000, tz=UTC).replace(
                        tzinfo=None
                    ),
                    open=Decimal(kline["o"]),
                    high=Decimal(kline["h"]),
                    low=Decimal(kline["l"]),
                    close=Decimal(kline["c"]),
                    volume=Decimal(kline["v"]),
                    symbol=symbol,
                )
                await self._queue.put(bar)

    async def close(self) -> None:
        self._running = False
