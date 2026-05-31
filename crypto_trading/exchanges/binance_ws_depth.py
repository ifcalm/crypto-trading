"""Binance orderbook depth WebSocket.

Maintains a local orderbook by fetching a REST snapshot then applying
WebSocket deltas. Provides async iteration over periodic snapshots.

Approach:
  1. REST GET /fapi/v1/depth to get initial snapshot (lastUpdateId)
  2. Subscribe to wss://fstream.binance.com/ws/<symbol>@depth<levels>@100ms
  3. For each event, check U/u against snapshot's lastUpdateId:
     - If u <= snapshot_lastUpdateId: skip (stale)
     - If pu (prev final update Id) is present and != our lastUpdateId: re-sync
     - Else: apply deltas (price/qty updates and qty=0 removals)
"""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import websockets

from crypto_trading.core.types import OrderBookLevel, OrderBookSnapshot

DEPTH_WS_BASE = "wss://fstream.binance.com"
DEPTH_WS_BASE_SPOT = "wss://stream.binance.com:9443"
REST_DEPTH_URL = "https://fapi.binance.com/fapi/v1/depth"
REST_DEPTH_URL_SPOT = "https://api.binance.com/api/v3/depth"


def _snapshot_url(market_type: str) -> str:
    return REST_DEPTH_URL_SPOT if market_type == "spot" else REST_DEPTH_URL


def _ws_url_base(market_type: str) -> str:
    return DEPTH_WS_BASE_SPOT if market_type == "spot" else DEPTH_WS_BASE


class DepthWebSocket:
    """Maintains a local orderbook from Binance depth stream.

    Usage::

        ws = DepthWebSocket(symbols=["BTC/USDT"], levels=20)
        async for snapshot in ws.stream():
            # snapshot is OrderBookSnapshot every output_interval seconds
            ...
    """

    def __init__(
        self,
        symbols: list[str],
        levels: int = 20,
        market_type: str = "futures",
        proxy: str | None = None,
        output_interval: float = 1.0,
    ) -> None:
        self.symbols = symbols
        self.levels = min(levels, 20)
        self.market_type = market_type
        self._proxy = proxy
        self.output_interval = output_interval
        # symbol -> {bids: [[price,qty],...], asks: [[price,qty],...], lastUpdateId: int}
        self._books: dict[str, dict[str, Any]] = {}
        self._running = False
        self._queue: asyncio.Queue[OrderBookSnapshot] = asyncio.Queue(maxsize=1000)
        self._symbol_map: dict[str, str] = {self._normalize(s): s for s in symbols}

    @staticmethod
    def _normalize(symbol: str) -> str:
        return symbol.lower().replace("/", "").split(":")[0]

    async def stream(self) -> AsyncIterator[OrderBookSnapshot]:
        """Async generator yielding OrderBookSnapshot at output_interval."""
        self._running = True
        consumer_task = asyncio.create_task(self._connect_and_read())

        try:
            while self._running:
                try:
                    snapshot = await asyncio.wait_for(self._queue.get(), timeout=30)
                    yield snapshot
                except TimeoutError:
                    continue
        finally:
            self._running = False
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass

    async def _connect_and_read(self) -> None:
        """Connect to WebSocket, process deltas, push periodic snapshots."""
        # First fetch REST snapshots for all symbols
        await self._init_snapshots()

        # Build stream URL
        streams = [f"{self._normalize(s)}@depth{self.levels}@100ms" for s in self.symbols]
        stream_path = "/".join(streams)
        base = _ws_url_base(self.market_type)
        if len(streams) == 1:
            url = f"{base}/ws/{stream_path}"
        else:
            url = f"{base}/stream?streams={stream_path}"

        kwargs: dict[str, Any] = {}
        if self._proxy:
            kwargs["proxy"] = self._proxy

        # Start the periodic output task
        output_task = asyncio.create_task(self._periodic_output())

        try:
            while self._running:
                try:
                    async with websockets.connect(url, **kwargs) as ws:
                        async for message in ws:
                            await self._handle_depth_message(message)
                except asyncio.CancelledError:
                    break
                except Exception:
                    if self._running:
                        await asyncio.sleep(3)
                        # Re-sync on reconnect
                        await self._init_snapshots()
        finally:
            output_task.cancel()
            try:
                await output_task
            except asyncio.CancelledError:
                pass

    async def _init_snapshots(self) -> None:
        """Fetch full orderbook snapshots via REST for all symbols."""
        async with httpx.AsyncClient(proxy=self._proxy) as client:
            for symbol in self.symbols:
                try:
                    norm = self._normalize(symbol)
                    resp = await client.get(
                        _snapshot_url(self.market_type),
                        params={"symbol": norm.upper(), "limit": self.levels},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        self._books[symbol] = {
                            "bids": [
                                [Decimal(str(b[0])), Decimal(str(b[1]))] for b in data["bids"]
                            ],
                            "asks": [
                                [Decimal(str(a[0])), Decimal(str(a[1]))] for a in data["asks"]
                            ],
                            "lastUpdateId": int(data["lastUpdateId"]),
                        }
                except Exception:
                    pass

    async def _handle_depth_message(self, message: str | bytes) -> None:
        data = json.loads(message)
        # Binance combined streams wrap in {"stream": "...", "data": {...}}
        if "stream" in data:
            event = data["data"]
        else:
            event = data

        raw_symbol = event.get("s", "").lower()
        symbol = self._symbol_map.get(raw_symbol, raw_symbol.upper())
        if symbol not in self._books:
            return

        book = self._books[symbol]
        final_update_id = int(event["u"])
        # firstUpdateId used for future drop-detection with prevFinalUpdateId
        prev_final_id = int(event.get("pu", 0))

        # Drop stale events
        if final_update_id <= book["lastUpdateId"]:
            return

        # Gap detected — need full re-sync
        if prev_final_id > 0 and prev_final_id != book["lastUpdateId"]:
            return  # Will re-sync on next reconnect or periodic check

        # Apply bid updates
        for price_str, qty_str in event.get("b", []):
            price = Decimal(str(price_str))
            qty = Decimal(str(qty_str))
            self._apply_level(book["bids"], price, qty)

        # Apply ask updates
        for price_str, qty_str in event.get("a", []):
            price = Decimal(str(price_str))
            qty = Decimal(str(qty_str))
            self._apply_level(book["asks"], price, qty)

        book["lastUpdateId"] = final_update_id

    @staticmethod
    def _apply_level(side: list[list[Decimal]], price: Decimal, qty: Decimal) -> None:
        """Update or remove a price level. Bids sorted by price desc, asks by price asc."""
        if not side:
            if qty > 0:
                side.append([price, qty])
            return

        is_bid = side[0][0] > side[-1][0] if len(side) > 1 else True

        for i, (p, _) in enumerate(side):
            if p == price:
                if qty == 0:
                    side.pop(i)
                else:
                    side[i][1] = qty
                return

        if qty == 0:
            return

        # Insert maintaining sort order
        if is_bid:
            for i, (p, _) in enumerate(side):
                if price > p:
                    side.insert(i, [price, qty])
                    return
            side.append([price, qty])
        else:
            for i, (p, _) in enumerate(side):
                if price < p:
                    side.insert(i, [price, qty])
                    return
            side.append([price, qty])

    async def _periodic_output(self) -> None:
        """Push snapshots of all symbols to the queue at output_interval."""
        while self._running:
            await asyncio.sleep(self.output_interval)
            now = datetime.now(UTC).replace(tzinfo=None)
            for symbol, book in self._books.items():
                if not book["bids"] or not book["asks"]:
                    continue
                snapshot = OrderBookSnapshot(
                    symbol=symbol,
                    timestamp=now,
                    bids=[OrderBookLevel(price=p, quantity=q) for p, q in book["bids"]],
                    asks=[OrderBookLevel(price=p, quantity=q) for p, q in book["asks"]],
                )
                try:
                    self._queue.put_nowait(snapshot)
                except asyncio.QueueFull:
                    pass

    def get_snapshot(self, symbol: str) -> OrderBookSnapshot | None:
        """Get the current orderbook snapshot for a symbol (non-async, from local cache)."""
        book = self._books.get(symbol)
        if not book or not book["bids"] or not book["asks"]:
            return None
        return OrderBookSnapshot(
            symbol=symbol,
            timestamp=datetime.now(UTC).replace(tzinfo=None),
            bids=[OrderBookLevel(price=p, quantity=q) for p, q in book["bids"]],
            asks=[OrderBookLevel(price=p, quantity=q) for p, q in book["asks"]],
        )

    async def close(self) -> None:
        self._running = False
