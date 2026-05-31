from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from crypto_trading.core.strategy import Strategy
from crypto_trading.core.types import (
    OHLCV,
    MarketType,
    OrderSide,
    OrderType,
    Portfolio,
    Position,
    PositionSide,
    Signal,
)
from crypto_trading.data.store import ParquetStore
from crypto_trading.risk.manager import RiskManager


@dataclass
class Trade:
    symbol: str
    side: PositionSide
    entry_time: datetime
    entry_price: Decimal
    exit_time: datetime | None = None
    exit_price: Decimal | None = None
    quantity: Decimal = Decimal("0")
    pnl: Decimal = Decimal("0")
    pnl_pct: Decimal = Decimal("0")
    fee: Decimal = Decimal("0")


@dataclass
class BacktestResult:
    equity_curve: list[tuple[datetime, Decimal]] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    initial_capital: Decimal = Decimal("0")
    final_equity: Decimal = Decimal("0")
    total_return: Decimal = Decimal("0")
    total_return_pct: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")


class BacktestEngine:
    def __init__(
        self,
        strategy: Strategy,
        store: ParquetStore,
        initial_capital: Decimal = Decimal("10000"),
        market_type: str = "futures",
        fee_rate: Decimal | None = None,
        leverage: int = 1,
        funding_rate: Decimal = Decimal("0.0001"),
        risk_manager: RiskManager | None = None,
        save_to_db: bool = False,
        db_url: str = "",
    ):
        self.strategy = strategy
        self.store = store
        self.initial_capital = initial_capital
        self.market_type = MarketType(market_type)
        self.fee_rate = fee_rate or (
            Decimal("0.0004") if market_type == "futures" else Decimal("0.001")
        )
        self.leverage = leverage
        self.funding_rate = funding_rate
        self.risk_manager = risk_manager
        self.save_to_db = save_to_db
        self.db_url = db_url
        self._strategy_name = type(strategy).__name__
        self._portfolio: Portfolio | None = None
        self._trades: list[Trade] = []
        self._equity_curve: list[tuple[datetime, Decimal]] = []
        self._open_trades: dict[str, Trade] = {}
        self._last_funding_time: datetime | None = None
        self._db_initialized = False
        self._pending_db_trades: list[Trade] = []

    async def run(
        self,
        symbols: list[str],
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> BacktestResult:
        all_bars: dict[str, list[OHLCV]] = {}
        for symbol in symbols:
            bars = self.store.read_ohlcv(symbol, timeframe, start, end)
            if bars:
                all_bars[symbol] = bars

        if not all_bars:
            return BacktestResult(initial_capital=self.initial_capital)

        timestamps = sorted({b.timestamp for bars in all_bars.values() for b in bars})
        bars_by_symbol_ts = {
            symbol: {bar.timestamp: bar for bar in bars} for symbol, bars in all_bars.items()
        }

        self._portfolio = Portfolio(
            total_equity=self.initial_capital,
            free_balance=self.initial_capital,
            peak_equity=self.initial_capital,
        )
        self._trades = []
        self._equity_curve = []
        self._open_trades = {}
        self._last_funding_time = None
        self._pending_db_trades = []

        if self.save_to_db and self.db_url:
            await self._init_db()

        await self.strategy.on_start()

        for ts in timestamps:
            for symbol in symbols:
                bar = bars_by_symbol_ts.get(symbol, {}).get(ts)
                if bar is None:
                    continue

                self.strategy._add_bar(symbol, bar)

                self._update_mark_price(symbol, bar.close)

                if self.market_type == MarketType.FUTURES:
                    self._settle_funding(ts)

                signal = await self.strategy.on_bar(symbol, bar)

                if signal is not None:
                    if signal.order_type == OrderType.MARKET or signal.price is None:
                        signal.price = bar.close
                    if self.risk_manager is not None:
                        signal = self.risk_manager.check_signal(signal, self._portfolio)
                    if signal is not None:
                        self._execute_signal(signal, bar)

            self._record_equity(ts)

        await self.strategy.on_stop()

        if self.save_to_db and self._pending_db_trades:
            for trade in self._pending_db_trades:
                try:
                    await self._save_trade_to_db(trade)
                except Exception:
                    import logging

                    logging.getLogger(__name__).warning("Failed to persist trade", exc_info=True)

        return BacktestResult(
            equity_curve=self._equity_curve,
            trades=self._trades,
            initial_capital=self.initial_capital,
            final_equity=self._portfolio.total_equity,
            total_return=self._portfolio.total_equity - self.initial_capital,
            total_return_pct=(
                (self._portfolio.total_equity - self.initial_capital) / self.initial_capital * 100
            ),
            total_fees=sum((t.fee for t in self._trades), Decimal("0")),
        )

    def _update_mark_price(self, symbol: str, price: Decimal) -> None:
        if self._portfolio is not None:
            self._portfolio.update_mark_prices({symbol: price})

    def _settle_funding(self, ts: datetime) -> None:
        if self._last_funding_time is None:
            self._last_funding_time = ts
            return

        hours_since = (ts - self._last_funding_time).total_seconds() / 3600
        if hours_since < 8:
            return

        if self._portfolio is None:
            return

        cycles = int(hours_since // 8)
        for pos in self._portfolio.positions.values():
            position_value = pos.mark_price * pos.quantity
            payment = position_value * self.funding_rate * Decimal(cycles)
            if pos.side == PositionSide.LONG:
                payment = -payment
            pos.funding_fee += payment
            self._portfolio.total_equity += payment

        self._last_funding_time = ts

    def _execute_signal(self, signal: Signal, bar: OHLCV) -> None:
        if self._portfolio is None:
            return

        if signal.amount <= 0:
            return

        if self.market_type == MarketType.SPOT:
            self._execute_spot(signal, bar)
        else:
            self._execute_futures(signal, bar)

    def _execute_spot(self, signal: Signal, bar: OHLCV) -> None:
        assert self._portfolio is not None
        fill_price = bar.close
        fee = signal.amount * fill_price * self.fee_rate
        existing = self._portfolio.positions.get(signal.symbol)

        if signal.side == OrderSide.SELL and existing is not None:
            proceeds = existing.quantity * fill_price - fee
            pnl = (fill_price - existing.entry_price) * existing.quantity - fee
            self._portfolio.free_balance += proceeds
            self._close_trade(signal.symbol, fill_price, ts=bar.timestamp, fee=fee, pnl=pnl)
            del self._portfolio.positions[signal.symbol]

        elif signal.side == OrderSide.BUY:
            cost = signal.amount * fill_price + fee
            if cost > self._portfolio.free_balance:
                return

            quantity = signal.amount
            self._portfolio.free_balance -= cost
            pos = Position(
                symbol=signal.symbol,
                side=PositionSide.LONG,
                quantity=quantity,
                entry_price=fill_price,
                mark_price=fill_price,
                leverage=1,
                margin=cost,
                unrealized_pnl=Decimal("0"),
            )
            self._portfolio.positions[signal.symbol] = pos
            self._open_trades[signal.symbol] = Trade(
                symbol=signal.symbol,
                side=PositionSide.LONG,
                entry_time=bar.timestamp,
                entry_price=fill_price,
                quantity=quantity,
                fee=fee,
            )

    def _execute_futures(self, signal: Signal, bar: OHLCV) -> None:
        assert self._portfolio is not None
        fill_price = bar.close
        notional = signal.amount * fill_price
        margin = notional / Decimal(self.leverage)
        fee = notional * self.fee_rate
        existing = self._portfolio.positions.get(signal.symbol)

        if signal.reduce_only:
            if existing is not None:
                self._close_futures_position(signal.symbol, fill_price, bar.timestamp, fee)
            return

        target_side = PositionSide.LONG if signal.side == OrderSide.BUY else PositionSide.SHORT

        if existing is not None and existing.side != target_side:
            self._close_futures_position(signal.symbol, fill_price, bar.timestamp, fee)
            existing = None

        if existing is not None and existing.side == target_side:
            return

        if margin + fee > self._portfolio.free_balance:
            return

        self._portfolio.free_balance -= margin + fee
        pos = Position(
            symbol=signal.symbol,
            side=target_side,
            quantity=signal.amount,
            entry_price=fill_price,
            mark_price=fill_price,
            leverage=self.leverage,
            margin=margin,
            unrealized_pnl=Decimal("0"),
        )
        self._portfolio.positions[signal.symbol] = pos
        self._open_trades[signal.symbol] = Trade(
            symbol=signal.symbol,
            side=target_side,
            entry_time=bar.timestamp,
            entry_price=fill_price,
            quantity=signal.amount,
            fee=fee,
        )

    def _close_futures_position(
        self, symbol: str, price: Decimal, ts: datetime, fee: Decimal
    ) -> None:
        assert self._portfolio is not None
        pos = self._portfolio.positions.get(symbol)
        if pos is None:
            return

        if pos.side == PositionSide.LONG:
            pnl = (price - pos.entry_price) * pos.quantity - fee
        else:
            pnl = (pos.entry_price - price) * pos.quantity - fee

        self._portfolio.free_balance += pos.margin + pnl

        trade = self._open_trades.pop(symbol, None)
        if trade:
            trade.exit_price = price
            trade.exit_time = ts
            trade.pnl = pnl
            trade.pnl_pct = pnl / (pos.margin) * 100 if pos.margin > 0 else Decimal("0")
            trade.fee += fee
            self._trades.append(trade)

        del self._portfolio.positions[symbol]

    def _close_trade(
        self,
        symbol: str,
        price: Decimal,
        ts: datetime,
        fee: Decimal = Decimal("0"),
        pnl: Decimal = Decimal("0"),
    ) -> None:
        trade = self._open_trades.pop(symbol, None)
        if trade:
            trade.exit_price = price
            trade.exit_time = ts
            trade.pnl = pnl
            trade.fee += fee
            if trade.entry_price > 0:
                trade.pnl_pct = pnl / (trade.entry_price * trade.quantity) * 100
            self._trades.append(trade)
            if self.save_to_db:
                self._pending_db_trades.append(trade)

    def _record_equity(self, ts: datetime) -> None:
        if self._portfolio is None:
            return
        equity = self._portfolio.recompute_equity()
        self._equity_curve.append((ts, equity))

    async def _init_db(self) -> None:
        if self._db_initialized:
            return
        from crypto_trading.data.database import init_db

        await init_db(self.db_url)
        self._db_initialized = True

    async def _save_trade_to_db(self, trade: Trade) -> None:
        try:
            from crypto_trading.data.repository import save_trade

            await save_trade(
                trade_id=trade.entry_time.isoformat() + "-" + trade.symbol.replace("/", "_"),
                strategy=self._strategy_name,
                symbol=trade.symbol,
                side=trade.side.value,
                entry_price=float(trade.entry_price),
                quantity=float(trade.quantity),
                entry_time=trade.entry_time,
                exit_price=float(trade.exit_price) if trade.exit_price else None,
                exit_time=trade.exit_time,
                pnl=float(trade.pnl),
                pnl_pct=float(trade.pnl_pct),
                fee=float(trade.fee),
                exchange="backtest",
                market_type=self.market_type.value,
                mode="backtest",
                leverage=self.leverage,
            )
        except Exception:
            import logging

            logging.getLogger(__name__).warning("Failed to persist trade to DB", exc_info=True)
