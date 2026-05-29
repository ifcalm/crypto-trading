"""Delta-neutral funding rate arbitrage.

Core logic:
    Open:  buy spot + short perp (same notional, zero net delta)
    Earn:  positive funding rate paid every 8 hours by perp longs
    Close: exit when funding rate drops below threshold or turns negative

The strategy runs its own timer loop — it is NOT bar-driven.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

from crypto_trading.core.errors import ExchangeError
from crypto_trading.core.exchange import Exchange
from crypto_trading.core.logging import get_logger
from crypto_trading.core.types import OrderSide, OrderType

log = get_logger(__name__)


class ArbState(Enum):
    IDLE = "idle"
    ACTIVE = "active"


@dataclass
class ArbPosition:
    symbol: str
    spot_amount: Decimal
    perp_amount: Decimal
    spot_entry_price: Decimal
    perp_entry_price: Decimal
    opened_at: datetime
    total_funding_collected: Decimal = Decimal("0")
    total_fees_paid: Decimal = Decimal("0")

    @property
    def notional(self) -> Decimal:
        return self.spot_amount * self.spot_entry_price


class FundingRateArbitrage:
    """Delta-neutral funding rate arbitrage service.

    Monitors funding rate on a futures exchange. When the rate exceeds
    min_funding_rate, opens a delta-neutral position (spot long + perp short).
    Exits when the rate drops below exit_funding_rate or goes negative.

    Usage::

        arb = FundingRateArbitrage(spot_ex=binance_spot, perp_ex=binance_futures)
        await arb.run()  # blocks until stopped
    """

    def __init__(
        self,
        spot_exchange: Exchange,
        futures_exchange: Exchange,
        *,
        symbol: str = "BTC/USDT",
        notional: Decimal | None = None,
        min_funding_rate: Decimal = Decimal("0.0001"),  # 0.01% per 8h
        exit_funding_rate: Decimal = Decimal("0"),       # exit when rate goes to zero
        check_interval: int = 300,  # seconds, 5 min
        leverage: int = 1,
    ):
        self.spot = spot_exchange
        self.futures = futures_exchange
        self.symbol = symbol
        self.notional = notional or Decimal("1000")  # $1000 per leg
        self.min_funding_rate = min_funding_rate
        self.exit_funding_rate = exit_funding_rate
        self.check_interval = check_interval
        self.leverage = leverage

        self._state = ArbState.IDLE
        self._position: ArbPosition | None = None
        self._running = False
        self._funding_history: list[tuple[datetime, Decimal]] = []

    # ─── main loop ──────────────────────────────────────────────────────

    async def run(self) -> None:
        """Start the arbitrage loop. Blocks until stop() is called."""
        self._running = True
        log.info(
            "funding_arb.started",
            symbol=self.symbol,
            notional=float(self.notional),
            min_rate=float(self.min_funding_rate),
        )

        await self.futures.set_leverage(self.symbol, self.leverage)

        while self._running:
            try:
                await self._tick()
            except Exception:
                log.exception("funding_arb.tick_error")
            await asyncio.sleep(self.check_interval)

    async def stop(self) -> None:
        self._running = False

    # ─── tick logic ─────────────────────────────────────────────────────

    async def _tick(self) -> None:
        funding_rate = await self.futures.fetch_funding_rate(self.symbol)
        self._funding_history.append((datetime.now(UTC).replace(tzinfo=None), funding_rate))

        log.debug(
            "funding_arb.tick",
            state=self._state.value,
            funding_rate=float(funding_rate),
        )

        if self._state == ArbState.IDLE:
            await self._try_open(funding_rate)
        elif self._state == ArbState.ACTIVE:
            await self._try_close(funding_rate)

    async def _try_open(self, rate: Decimal) -> None:
        if rate < self.min_funding_rate:
            return

        # Calculate amounts
        spot_price = (await self.spot.fetch_ticker(self.symbol)).last
        perp_price = (await self.futures.fetch_ticker(self.symbol)).last

        if spot_price <= 0 or perp_price <= 0:
            return

        spot_amount = self.notional / spot_price
        perp_amount = self.notional / perp_price

        spot_fee = spot_price * spot_amount * self.spot.trading_fee
        perp_fee = perp_price * perp_amount * self.futures.trading_fee
        total_fee = spot_fee + perp_fee

        log.info(
            "funding_arb.opening",
            rate=float(rate),
            spot_price=float(spot_price),
            perp_price=float(perp_price),
            amount=float(spot_amount),
            fee=float(total_fee),
        )

        try:
            # Buy spot (market)
            spot_order = await self.spot.create_order(
                symbol=self.symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                amount=spot_amount,
            )

            # Short perp (market)
            perp_order = await self.futures.create_order(
                symbol=self.symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                amount=perp_amount,
                reduce_only=False,
            )
        except ExchangeError as e:
            log.error("funding_arb.open_failed", error=str(e))
            return

        self._position = ArbPosition(
            symbol=self.symbol,
            spot_amount=spot_amount,
            perp_amount=perp_amount,
            spot_entry_price=spot_price,
            perp_entry_price=perp_price,
            opened_at=datetime.now(UTC).replace(tzinfo=None),
            total_fees_paid=total_fee,
        )
        self._state = ArbState.ACTIVE
        log.info("funding_arb.opened", spot_oid=spot_order.id, perp_oid=perp_order.id)

    async def _try_close(self, rate: Decimal) -> None:
        if rate > self.exit_funding_rate and rate >= 0:
            # Still profitable — collect funding and stay
            if self._position is not None:
                funding_payment = self._position.notional * rate
                self._position.total_funding_collected += funding_payment
            return

        pos = self._position
        if pos is None:
            self._state = ArbState.IDLE
            return

        log.info(
            "funding_arb.closing",
            rate=float(rate),
            funding_collected=float(pos.total_funding_collected),
        )

        try:
            # Sell spot
            await self.spot.create_order(
                symbol=self.symbol,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                amount=pos.spot_amount,
            )

            # Buy back perp (close short)
            await self.futures.create_order(
                symbol=self.symbol,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                amount=pos.perp_amount,
                reduce_only=True,
            )
        except ExchangeError as e:
            log.error("funding_arb.close_failed", error=str(e))
            return

        # Calculate PnL
        spot_price = (await self.spot.fetch_ticker(self.symbol)).last
        perp_price = (await self.futures.fetch_ticker(self.symbol)).last

        spot_pnl = (spot_price - pos.spot_entry_price) * pos.spot_amount
        perp_pnl = (pos.perp_entry_price - perp_price) * pos.perp_amount
        net_pnl = spot_pnl + perp_pnl + pos.total_funding_collected - pos.total_fees_paid

        log.info(
            "funding_arb.closed",
            spot_pnl=float(spot_pnl),
            perp_pnl=float(perp_pnl),
            funding_collected=float(pos.total_funding_collected),
            fees=float(pos.total_fees_paid),
            net_pnl=float(net_pnl),
            duration_hours=(
                datetime.now(UTC).replace(tzinfo=None) - pos.opened_at
            ).total_seconds()
            / 3600,
        )

        self._position = None
        self._state = ArbState.IDLE

    # ─── query ──────────────────────────────────────────────────────────

    @property
    def state(self) -> ArbState:
        return self._state

    @property
    def position(self) -> ArbPosition | None:
        return self._position

    @property
    def funding_history(self) -> list[tuple[datetime, Decimal]]:
        return self._funding_history
