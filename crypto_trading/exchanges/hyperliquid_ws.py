"""Hyperliquid WebSocket adapter for real-time OHLCV bars.

The HL SDK uses a callback-based subscription system. We bridge this to an
async generator (Queue) to match the BinanceWebSocket interface and work
seamlessly with the LiveTradingRunner.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from hyperliquid.info import Info as HLInfo
from hyperliquid.utils import constants as hl_constants
from hyperliquid.utils.types import CandleSubscription

from crypto_trading.core.types import OHLCV


def _to_hl_symbol(symbol: str) -> str:
    return symbol.split("/")[0].upper()


def _from_hl_symbol(hl_name: str) -> str:
    return f"{hl_name.upper()}/USDT"


class HyperliquidWebSocket:
    """WebSocket client for Hyperliquid real-time kline data.

    Usage: identical to BinanceWebSocket::

        ws = HyperliquidWebSocket(symbols=["BTC/USDT"], timeframes=["15m"])
        async for bar in ws.stream():
            ...
    """

    def __init__(
        self,
        symbols: list[str],
        timeframes: list[str],
        market_type: str = "futures",
        testnet: bool = False,
    ) -> None:
        self.symbols = symbols
        self.timeframes = timeframes
        self.market_type = market_type
        self._testnet = testnet
        self._queue: asyncio.Queue[OHLCV] = asyncio.Queue()
        self._running = False
        self._info: HLInfo | None = None
        self._subscription_ids: list[int] = []

    async def stream(self) -> AsyncIterator[OHLCV]:
        """Async generator yielding completed OHLCV bars."""
        self._running = True

        base_url = hl_constants.TESTNET_API_URL if self._testnet else hl_constants.MAINNET_API_URL
        self._info = HLInfo(base_url, skip_ws=False)

        # Subscribe to klines for each symbol × timeframe
        for symbol in self.symbols:
            hl_name = _to_hl_symbol(symbol)
            for tf in self.timeframes:
                interval = self._normalize_tf(tf)
                sub_id = self._info.subscribe(
                    CandleSubscription(coin=hl_name, interval=interval),
                    self._on_candle,
                )
                self._subscription_ids.append(sub_id)

        try:
            while self._running:
                try:
                    bar = await asyncio.wait_for(self._queue.get(), timeout=30)
                    yield bar
                except TimeoutError:
                    continue
        finally:
            self._running = False
            await self.close()

    def _on_candle(self, data: dict[str, Any]) -> None:
        """Callback from HL SDK WebSocket subscription."""
        if not self._running:
            return

        try:
            coin = data.get("coin", "")
            # Only process closed candles
            if not data.get("closed", False):
                return

            symbol = _from_hl_symbol(coin)
            bar = OHLCV(
                timestamp=datetime.fromtimestamp(data["t"] / 1000, tz=UTC).replace(tzinfo=None),
                open=Decimal(str(data["o"])),
                high=Decimal(str(data["h"])),
                low=Decimal(str(data["l"])),
                close=Decimal(str(data["c"])),
                volume=Decimal(str(data["v"])),
                symbol=symbol,
                metadata={"timeframe": data.get("interval", "")},
            )
            self._queue.put_nowait(bar)
        except asyncio.QueueFull:
            pass
        except Exception:
            pass

    @staticmethod
    def _normalize_tf(tf: str) -> str:
        """Normalize timeframe for HL: '15m' -> '15m', '1h' -> '1h', '5m' -> '5m'"""
        return tf

    async def close(self) -> None:
        self._running = False
        if self._info:
            for sub_id in self._subscription_ids:
                try:
                    self._info.unsubscribe(CandleSubscription(coin="", interval=""), sub_id)
                except Exception:
                    pass
            try:
                self._info.disconnect_websocket()
            except Exception:
                pass
            self._info = None
