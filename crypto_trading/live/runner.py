import asyncio
import json
import signal as sys_signal
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from crypto_trading.core.logging import get_logger
from crypto_trading.core.strategy import Strategy
from crypto_trading.core.types import OHLCV, Order, OrderBookSnapshot, OrderType, Portfolio, Signal
from crypto_trading.data.store import ParquetStore
from crypto_trading.exchanges.binance_ws import BinanceWebSocket
from crypto_trading.exchanges.binance_ws_depth import DepthWebSocket
from crypto_trading.execution.broker import Broker
from crypto_trading.risk.manager import RiskManager

log = get_logger(__name__)

STATUS_FILE = Path("data/status.json")

STATUS_FILE = Path("data/status.json")


class LiveTradingRunner:
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
    ) -> None:
        self.strategy = strategy
        self.broker = broker
        self.ws = ws_client
        self.store = store
        self.risk_manager = risk_manager
        self.initial_capital = initial_capital
        self.depth_ws = depth_ws
        self.db_url = db_url
        self.market_type = market_type
        self._strategy_name = type(strategy).__name__
        self._db_initialized = False
        self._portfolio = Portfolio(
            total_equity=initial_capital,
            free_balance=initial_capital,
            peak_equity=initial_capital,
        )
        self._bar_count = 0
        self._started_at: datetime | None = None
        self._running = False
        self._recent_trades: list[dict[str, Any]] = []
        self._equity_history: list[dict[str, Any]] = []
        self._equity_sample_interval = 10

    @property
    def portfolio(self) -> Portfolio:
        return self._portfolio

    async def run(self) -> None:
        self._running = True
        self._started_at = datetime.now(UTC).replace(tzinfo=None)
        loop = asyncio.get_event_loop()
        stop_event = asyncio.Event()

        for sig in (sys_signal.SIGINT, sys_signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                pass

        if self.db_url:
            await self._init_db()

        await self.strategy.on_start()
        self._write_status()

        async def consume_bars() -> None:
            async for bar in self.ws.stream():
                if stop_event.is_set():
                    break
                await self._on_bar(bar)

        async def consume_depth() -> None:
            if self.depth_ws is None:
                return
            async for snapshot in self.depth_ws.stream():
                if stop_event.is_set():
                    break
                self._on_orderbook(snapshot)

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
        self._write_status()
        await self.strategy.on_stop()
        await self.ws.close()
        if self.depth_ws:
            await self.depth_ws.close()
        await self.broker.close()

    async def _on_bar(self, bar: OHLCV) -> None:
        self._bar_count += 1
        symbol = bar.symbol
        if not symbol:
            return

        self.strategy._add_bar(symbol, bar)

        signal = await self.strategy.on_bar(symbol, bar)

        if signal is not None:
            log.debug(
                "signal.generated",
                symbol=symbol,
                side=signal.side.value,
                amount=float(signal.amount),
            )
            if signal.order_type == OrderType.MARKET or signal.price is None:
                signal.price = bar.close
            if self.risk_manager is not None:
                signal = self.risk_manager.check_signal(signal, self._portfolio)
            if signal is not None:
                order = await self.broker.execute_signal(signal, self._portfolio)
                if order is not None:
                    log.info(
                        "trade.executed",
                        symbol=symbol,
                        side=order.side.value,
                        amount=float(order.amount),
                        price=float(order.price or 0),
                    )
                    self._record_trade(signal, bar)
                    if self.db_url:
                        asyncio.create_task(self._save_order_to_db(order))

        self._update_equity(bar)

        if self._bar_count % self._equity_sample_interval == 0:
            self._equity_history.append(
                {
                    "time": bar.timestamp.isoformat(),
                    "equity": float(self._portfolio.total_equity),
                }
            )

        self._write_status()

    def _on_orderbook(self, snapshot: OrderBookSnapshot) -> None:
        """Feed orderbook snapshot to the strategy if it supports it."""
        on_ob = getattr(self.strategy, "on_orderbook", None)
        if callable(on_ob):
            on_ob(snapshot)

    def _record_trade(self, signal: Signal, bar: OHLCV) -> None:
        entry = {
            "time": bar.timestamp.isoformat(),
            "symbol": signal.symbol,
            "side": signal.side.value.upper(),
            "qty": float(signal.amount),
            "price": float(bar.close),
            "pnl": None,
        }
        self._recent_trades.append(entry)
        if len(self._recent_trades) > 200:
            self._recent_trades = self._recent_trades[-200:]

        print(
            f"[{bar.timestamp.strftime('%Y-%m-%d %H:%M')}] "
            f"{entry['side']} {entry['symbol']} "
            f"qty={entry['qty']:.4f} @ {entry['price']:.2f}"
        )

    def _update_equity(self, bar: OHLCV) -> None:
        equity = self._portfolio.free_balance
        for pos in self._portfolio.positions.values():
            pos.mark_price = bar.close
            if pos.side.value == "long":
                pos.unrealized_pnl = (bar.close - pos.entry_price) * pos.quantity
            else:
                pos.unrealized_pnl = (pos.entry_price - bar.close) * pos.quantity
            equity += pos.margin + pos.unrealized_pnl
        self._portfolio.total_equity = equity
        if equity > self._portfolio.peak_equity:
            self._portfolio.peak_equity = equity
            self._portfolio.current_drawdown = Decimal("0")
        elif self._portfolio.peak_equity > 0:
            self._portfolio.current_drawdown = (
                self._portfolio.peak_equity - equity
            ) / self._portfolio.peak_equity

    def _write_status(self) -> None:
        positions = []
        for pos in self._portfolio.positions.values():
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

        equity = float(self._portfolio.total_equity)
        pnl = equity - float(self.initial_capital)
        pnl_pct = pnl / float(self.initial_capital) * 100 if float(self.initial_capital) > 0 else 0

        status = {
            "running": self._running,
            "bar_count": self._bar_count,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "equity": equity,
            "initial_capital": float(self.initial_capital),
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "drawdown": float(self._portfolio.current_drawdown),
            "positions": positions,
            "recent_trades": self._recent_trades[-50:],
            "equity_history": self._equity_history[-200:],
        }

        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATUS_FILE, "w") as f:
            json.dump(status, f, indent=2)

    async def _init_db(self) -> None:
        if self._db_initialized:
            return
        from crypto_trading.data.database import init_db

        await init_db(self.db_url)
        self._db_initialized = True

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
