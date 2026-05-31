"""Simple market making strategy.

Places bid/ask limit orders around the mid price, adjusts spread
based on inventory, and replaces unfilled orders on each cycle.

Runs its own timer loop — not bar-driven.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from enum import Enum

from crypto_trading.core.errors import ExchangeError
from crypto_trading.core.exchange import Exchange
from crypto_trading.core.logging import get_logger
from crypto_trading.core.types import (
    OrderSide,
    OrderType,
    PositionSide,
)

log = get_logger(__name__)


class MMState(Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"  # e.g. inventory too high


class MarketMaker:
    """Simple inventory-aware market maker.

    Each cycle:
      1. Cancel all open orders for the symbol
      2. Compute fair price (mid of best bid/ask)
      3. Compute spread based on inventory skew
      4. Place new bid and ask limit orders
      5. Sleep for cycle_interval

    Usage::

        mm = MarketMaker(exchange=binance, symbol="BTC/USDT")
        await mm.run()  # blocks until stopped
    """

    def __init__(
        self,
        exchange: Exchange,
        *,
        symbol: str = "BTC/USDT",
        quote_size: Decimal | None = None,
        base_spread_bps: float = 5.0,  # 0.05% half-spread from mid
        max_spread_bps: float = 50.0,  # max half-spread when skewed
        max_inventory: Decimal | None = None,
        cycle_interval: float = 2.0,  # seconds between order replacements
        position_limits: tuple[int, int] = (-5, 5),  # (min, max) net position
        leverage: int = 1,
        paper: bool = True,
    ):
        self.exchange = exchange
        self.symbol = symbol
        self.quote_size = quote_size or Decimal("0.001")  # BTC
        self.base_half_spread = Decimal(str(base_spread_bps / 10_000))
        self.max_half_spread = Decimal(str(max_spread_bps / 10_000))
        if max_inventory:
            self.max_inventory = max_inventory
        else:
            self.max_inventory = self.quote_size * Decimal(str(abs(position_limits[1])))
        self.cycle_interval = cycle_interval
        self.position_min, self.position_max = position_limits
        self.leverage = leverage
        self.paper = paper

        self._state = MMState.STOPPED
        self._running = False
        self._active_bid_id: str | None = None
        self._active_ask_id: str | None = None
        self._net_position: Decimal = Decimal("0")  # positive = long inventory
        self._total_pnl: Decimal = Decimal("0")
        self._total_fees: Decimal = Decimal("0")
        self._cycle_count = 0
        self._trade_count = 0

    # ─── main loop ───────────────────────────────────────────────────────

    async def run(self) -> None:
        self._running = True
        self._state = MMState.RUNNING
        log.info("market_maker.started", symbol=self.symbol)

        await self.exchange.set_leverage(self.symbol, self.leverage)

        while self._running:
            try:
                await self._cycle()
            except Exception:
                log.exception("market_maker.cycle_error")
                await asyncio.sleep(1.0)
            await asyncio.sleep(self.cycle_interval)

    async def stop(self) -> None:
        self._running = False
        try:
            await self._cancel_all()
        except Exception:
            pass
        self._state = MMState.STOPPED
        log.info("market_maker.stopped", pnl=float(self._total_pnl), fees=float(self._total_fees))

    # ─── cycle ───────────────────────────────────────────────────────────

    async def _cycle(self) -> None:
        self._cycle_count += 1

        # 1. Cancel existing orders
        await self._cancel_all()

        # 2. Check inventory limits — pause if breached
        await self._sync_position()
        if self._net_position <= self.position_min or self._net_position >= self.position_max:
            if self._state != MMState.PAUSED:
                log.warning("market_maker.paused", position=float(self._net_position))
                self._state = MMState.PAUSED
            return
        self._state = MMState.RUNNING

        # 3. Get fair price
        ticker = await self.exchange.fetch_ticker(self.symbol)
        mid = (ticker.bid + ticker.ask) / 2
        if mid <= 0:
            return

        # 4. Compute spread (wider when inventory skewed)
        inventory_ratio = (
            self._net_position / self.max_inventory if self.max_inventory > 0 else Decimal("0")
        )
        half_spread = self.base_half_spread + abs(inventory_ratio) * (
            self.max_half_spread - self.base_half_spread
        )

        # Skew: if long, shift both prices lower (encourage selling); if short, shift higher
        skew = inventory_ratio * half_spread

        bid_price = mid * (Decimal("1") - half_spread + skew)
        ask_price = mid * (Decimal("1") + half_spread + skew)

        # 5. Place new orders
        try:
            bid_order = await self.exchange.create_order(
                symbol=self.symbol,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                amount=self.quote_size,
                price=bid_price,
            )
            self._active_bid_id = bid_order.id

            ask_order = await self.exchange.create_order(
                symbol=self.symbol,
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                amount=self.quote_size,
                price=ask_price,
            )
            self._active_ask_id = ask_order.id
        except ExchangeError as e:
            log.warning("market_maker.order_failed", error=str(e))

        if self._cycle_count % 30 == 0:
            log.info(
                "market_maker.status",
                cycle=self._cycle_count,
                mid=float(mid),
                spread_bps=float(half_spread) * 10_000,
                pos=float(self._net_position),
                pnl=float(self._total_pnl),
            )

    # ─── helpers ─────────────────────────────────────────────────────────

    async def _cancel_all(self) -> None:
        for oid in (self._active_bid_id, self._active_ask_id):
            if oid:
                try:
                    await self.exchange.cancel_order(oid, self.symbol)
                except Exception:
                    pass
        self._active_bid_id = None
        self._active_ask_id = None

    async def _sync_position(self) -> None:
        """Query current position from exchange and update inventory."""
        try:
            positions = await self.exchange.fetch_positions()
            for pos in positions:
                if pos.symbol == self.symbol:
                    if pos.side == PositionSide.LONG:
                        self._net_position = pos.quantity
                    else:
                        self._net_position = -pos.quantity
                    self._total_pnl = pos.unrealized_pnl + pos.realized_pnl
                    return
            self._net_position = Decimal("0")
        except Exception:
            pass

    # ─── query ───────────────────────────────────────────────────────────

    @property
    def state(self) -> MMState:
        return self._state

    @property
    def net_position(self) -> Decimal:
        return self._net_position

    @property
    def total_pnl(self) -> Decimal:
        return self._total_pnl

    @property
    def cycle_count(self) -> int:
        return self._cycle_count
