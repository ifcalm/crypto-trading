import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import websockets

from crypto_trading.core.types import OHLCV

# Binance WebSocket base URLs
WS_BASE = "wss://fapi.binance.com"  # Futures
WS_BASE_SPOT = "wss://stream.binance.com:9443"  # Spot


class BinanceWebSocket:
    def __init__(
        self,
        symbols: list[str],
        timeframes: list[str],
        market_type: str = "futures",
        proxy: str | None = None,
    ) -> None:
        self.symbols = symbols
        self.timeframes = timeframes
        self.market_type = market_type
        self._ws_url = WS_BASE_SPOT if market_type == "spot" else WS_BASE
        self._proxy = proxy
        self._queue: asyncio.Queue[OHLCV] = asyncio.Queue(maxsize=1000)
        self._running = False
        self._ws: Any = None
        # normalized_name -> original symbol for reverse lookup
        self._symbol_map: dict[str, str] = {self._normalize(s): s for s in symbols}

    @staticmethod
    def _normalize(symbol: str) -> str:
        s = symbol.lower().replace("/", "")
        if ":" in s:
            s = s.split(":")[0]
        return s

    async def stream(self) -> AsyncIterator[OHLCV]:
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
            norm = self._normalize(symbol)
            for tf in self.timeframes:
                streams.append(f"{norm}@kline_{tf}")

        stream_path = "/".join(streams)
        if len(streams) == 1:
            url = f"{self._ws_url}/ws/{stream_path}"
        else:
            url = f"{self._ws_url}/stream?streams={stream_path}"

        kwargs: dict[str, Any] = {}
        if self._proxy:
            kwargs["proxy"] = self._proxy

        while self._running:
            try:
                async with websockets.connect(url, **kwargs) as ws:
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
                raw_symbol = kline["s"].lower()
                symbol = self._symbol_map.get(raw_symbol, raw_symbol.upper())
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
                    metadata={"timeframe": kline.get("i", "")},
                )
                await self._queue.put(bar)

    async def close(self) -> None:
        self._running = False
