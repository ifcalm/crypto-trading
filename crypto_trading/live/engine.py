"""Event-driven trading pipeline.

Replaces the monolithic LiveTradingRunner._on_bar with a composable
handler chain driven by domain events.

Architecture:
    BarArrived -> Strategy -> SignalGenerated
    SignalGenerated -> Risk -> OrderRequested | SignalRejected
    OrderRequested -> Broker -> OrderExecuted
    OrderExecuted -> Portfolio -> PositionOpened | PositionClosed
"""

from __future__ import annotations

import asyncio
import json
import signal as sys_signal
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from crypto_trading.core.events import (
    BarArrived,
    EventBus,
    OrderExecuted,
    OrderRequested,
    SignalGenerated,
    SignalRejected,
)
from crypto_trading.core.logging import get_logger
from crypto_trading.core.strategy import Strategy
from crypto_trading.core.types import (
    Order,
    OrderType,
    Portfolio,
)
from crypto_trading.data.store import ParquetStore
from crypto_trading.exchanges.binance_ws import BinanceWebSocket
from crypto_trading.exchanges.binance_ws_depth import DepthWebSocket
from crypto_trading.execution.broker import Broker
from crypto_trading.risk.manager import RiskManager

log = get_logger(__name__)
STATUS_FILE = Path("data/status.json")


# ─── pipeline handlers ──────────────────────────────────────────────────


class PipelineHandlers:
    """Standalone async handlers for the trading pipeline.
    Each handler receives one event type and publishes follow-up events.
    """

    def __init__(
        self,
        strategy: Strategy,
        broker: Broker,
        risk_manager: RiskManager | None,
        portfolio: Portfolio,
        store: ParquetStore,
        db_url: str = "",
        market_type: str = "futures",
    ):
        self.strategy = strategy
        self.broker = broker
        self.risk_manager = risk_manager
        self.portfolio = portfolio
        self.store = store
        self.db_url = db_url
        self.market_type = market_type
        self._strategy_name = type(strategy).__name__
        self._bar_count = 0
        self._recent_trades: list[dict] = []
        self._equity_history: list[dict] = []
        self._equity_sample_interval = 10

    # ─── BarArrived -> SignalGenerated ──────────────────────────────────

    async def on_bar(self, event: BarArrived) -> None:
        self._bar_count += 1
        bar = event.bar
        symbol = event.symbol
        if not bar or not symbol:
            return

        self.strategy._add_bar(symbol, bar)

        # Update mark prices for unrealized PnL
        self.portfolio.update_mark_prices({symbol: bar.close})

        signal = await self.strategy.on_bar(symbol, bar)

        if signal is not None:
            signal.price = (
                bar.close
                if (signal.order_type == OrderType.MARKET or signal.price is None)
                else signal.price
            )
            await self._bus.publish(SignalGenerated(symbol=symbol, signal=signal))

        # Periodic equity snapshot
        if self._bar_count % self._equity_sample_interval == 0:
            self._equity_history.append(
                {
                    "time": bar.timestamp.isoformat(),
                    "equity": float(self.portfolio.total_equity),
                }
            )

    # ─── SignalGenerated -> OrderRequested | SignalRejected ─────────────

    async def on_signal(self, event: SignalGenerated) -> None:
        signal = event.signal
        if signal is None:
            return

        if self.risk_manager is not None:
            result = self.risk_manager.check_signal(signal, self.portfolio)
            if result is None:
                await self._bus.publish(
                    SignalRejected(symbol=event.symbol, signal=signal, reason="risk")
                )
                return
            signal = result

        await self._bus.publish(
            OrderRequested(symbol=event.symbol, signal=signal, portfolio=self.portfolio)
        )

    # ─── OrderRequested -> OrderExecuted ────────────────────────────────

    async def on_order_requested(self, event: OrderRequested) -> None:
        signal = event.signal
        if signal is None:
            return

        order = await self.broker.execute_signal(signal, self.portfolio)
        if order is None:
            return

        await self._bus.publish(OrderExecuted(symbol=event.symbol, order=order, signal=signal))

    # ─── OrderExecuted -> PositionOpened | PositionClosed ───────────────

    async def on_order_executed(self, event: OrderExecuted) -> None:
        order = event.order
        if order is None:
            return

        self._recent_trades.append(
            {
                "time": order.timestamp.isoformat(),
                "symbol": order.symbol,
                "side": order.side.value.upper(),
                "qty": float(order.amount),
                "price": float(order.price or 0),
                "pnl": None,
            }
        )
        if len(self._recent_trades) > 200:
            self._recent_trades = self._recent_trades[-200:]

        log.info(
            "trade.executed",
            symbol=order.symbol,
            side=order.side.value,
            amount=float(order.amount),
            price=float(order.price or 0),
        )

        self.portfolio.recompute_equity()

        # Persist to DB
        if self.db_url:
            asyncio.create_task(self._save_order_to_db(order))

    # ─── event bus wiring ───────────────────────────────────────────────

    def wire(self, bus: EventBus) -> None:
        """Register all handlers on the event bus."""
        self._bus = bus
        bus.subscribe(BarArrived, self.on_bar)
        bus.subscribe(SignalGenerated, self.on_signal)
        bus.subscribe(OrderRequested, self.on_order_requested)
        bus.subscribe(OrderExecuted, self.on_order_executed)

    # ─── status / persistence ───────────────────────────────────────────

    def write_status(
        self, running: bool, started_at: datetime | None, initial_capital: Decimal
    ) -> None:
        positions = []
        for pos in self.portfolio.positions.values():
            positions.append(
                {
                    "symbol": pos.symbol,
                    "side": pos.side.value,
                    "quantity": float(pos.quantity),
                    "entry_price": float(pos.entry_price),
                    "mark_price": float(pos.mark_price),
                    "leverage": pos.leverage,
                    "margin": float(pos.margin),
                    "unrealized_pnl": float(pos.unrealized_pnl),
                    "funding_fee": float(pos.funding_fee),
                }
            )

        equity = float(self.portfolio.total_equity)
        initial = float(initial_capital)
        pnl = equity - initial
        pnl_pct = pnl / initial * 100 if initial > 0 else 0

        status = {
            "running": running,
            "bar_count": self._bar_count,
            "started_at": started_at.isoformat() if started_at else None,
            "equity": equity,
            "initial_capital": initial,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "drawdown": float(self.portfolio.current_drawdown),
            "positions": positions,
            "recent_trades": self._recent_trades[-50:],
            "equity_history": self._equity_history[-200:],
        }

        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATUS_FILE, "w") as f:
            json.dump(status, f, indent=2)

    async def _save_order_to_db(self, order: Order) -> None:
        try:
            from crypto_trading.data.repository import save_order

            await save_order(
                order=order,
                strategy=self._strategy_name,
                exchange="binance",
                market_type=self.market_type,
                mode="paper" if hasattr(self.broker, "slippage") else "live",
            )
        except Exception:
            log.warning("Failed to persist order to DB", exc_info=True)


# ─── event-driven runner ────────────────────────────────────────────────


class EventDrivenRunner:
    """Thin orchestrator: starts WS streams, publishes bar events, manages lifecycle."""

    def __init__(
        self,
        strategy: Strategy,
        broker: Broker,
        ws_client: BinanceWebSocket,
        store: ParquetStore,
        risk_manager: RiskManager | None = None,
        initial_capital: Decimal = Decimal("10000"),
        depth_ws: DepthWebSocket | None = None,
        db_url: str = "",
        market_type: str = "futures",
    ):
        self.strategy = strategy
        self.broker = broker
        self.ws = ws_client
        self.store = store
        self.risk_manager = risk_manager
        self.initial_capital = initial_capital
        self.depth_ws = depth_ws
        self.db_url = db_url
        self.market_type = market_type

        self.portfolio = Portfolio(
            total_equity=initial_capital,
            free_balance=initial_capital,
            peak_equity=initial_capital,
        )
        self.bus = EventBus()
        self.pipeline = PipelineHandlers(
            strategy=strategy,
            broker=broker,
            risk_manager=risk_manager,
            portfolio=self.portfolio,
            store=store,
            db_url=db_url,
            market_type=market_type,
        )
        self._running = False
        self._started_at: datetime | None = None

    async def run(self) -> None:
        self._running = True
        self._started_at = datetime.now(UTC).replace(tzinfo=None)

        # Wire pipeline handlers
        self.pipeline.wire(self.bus)

        loop = asyncio.get_event_loop()
        stop_event = asyncio.Event()

        for sig in (sys_signal.SIGINT, sys_signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                pass

        await self.strategy.on_start()
        self.pipeline.write_status(True, self._started_at, self.initial_capital)

        async def consume_bars() -> None:
            async for bar in self.ws.stream():
                if stop_event.is_set():
                    break
                await self.bus.publish(BarArrived(symbol=bar.symbol or "", bar=bar))

        async def consume_depth() -> None:
            if self.depth_ws is None:
                return
            async for snapshot in self.depth_ws.stream():
                if stop_event.is_set():
                    break
                on_ob = getattr(self.strategy, "on_orderbook", None)
                if callable(on_ob):
                    on_ob(snapshot)

        bar_task = asyncio.create_task(consume_bars())
        depth_task = asyncio.create_task(consume_depth())

        await stop_event.wait()

        for task in (bar_task, depth_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._running = False
        self.pipeline.write_status(False, self._started_at, self.initial_capital)
        await self.strategy.on_stop()
        await self.ws.close()
        if self.depth_ws:
            await self.depth_ws.close()
        await self.broker.close()
