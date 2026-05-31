"""Backtest page — configure and run strategy backtests."""

import asyncio
from datetime import timedelta
from decimal import Decimal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from crypto_trading.backtest.engine import BacktestEngine
from crypto_trading.backtest.metrics import calculate_all
from crypto_trading.config.settings import load_settings
from crypto_trading.data.store import ParquetStore
from crypto_trading.risk.manager import RiskManager
from crypto_trading.risk.rules import (
    MaxDrawdownRule,
    MaxLeverageRule,
    MaxOpenPositionsRule,
    MinConfidenceRule,
    PositionSizeRule,
)
from crypto_trading.strategies import get_strategy, list_strategies

st.set_page_config(page_title="Backtest", page_icon="🧪", layout="wide")
st.title("Strategy Backtest")

settings = load_settings()

with st.sidebar:
    st.header("Configuration")

    strategy_name = st.selectbox("Strategy", list_strategies())

    symbol = st.text_input("Symbol", settings.trading.symbols[0])

    market = st.selectbox(
        "Market", ["futures", "spot"], index=0 if settings.market_type == "futures" else 1
    )

    timeframe = st.selectbox("Timeframe", ["1m", "5m", "15m", "1h", "4h", "1d"], index=3)

    date_range = st.date_input(
        "Date Range",
        value=(pd.Timestamp.now() - timedelta(days=365), pd.Timestamp.now()),
        max_value=pd.Timestamp.now(),
    )

    col_a, col_b = st.columns(2)
    capital = col_a.number_input("Capital ($)", min_value=100, value=10000, step=1000)
    leverage = col_b.number_input("Leverage", min_value=1, max_value=125, value=1, step=1)

    st.divider()

    st.subheader("Risk Rules")
    max_drawdown = st.slider("Max Drawdown %", 1, 100, 20) / 100
    max_position = st.slider("Max Position %", 1, 100, 10) / 100
    max_positions = st.number_input("Max Open Positions", 1, 50, 5)
    min_confidence = st.slider("Min Confidence", 0.0, 1.0, 0.5, 0.05)

    run_btn = st.button("Run Backtest", type="primary", use_container_width=True)

if run_btn:
    if len(date_range) != 2:
        st.error("Please select a date range")
    else:
        start_dt = pd.Timestamp(date_range[0]).to_pydatetime()
        end_dt = pd.Timestamp(date_range[1]).to_pydatetime()

        store = ParquetStore(base_dir=settings.data.parquet_dir)

        strategy_params = settings.strategy_params.get(strategy_name, {})
        strategy = get_strategy(name=strategy_name, symbols=[symbol], params=strategy_params)

        risk_manager = RiskManager(
            [
                MaxDrawdownRule(max_drawdown_pct=max_drawdown),
                PositionSizeRule(max_position_pct=max_position),
                MaxOpenPositionsRule(max_positions=max_positions),
                MinConfidenceRule(min_confidence=min_confidence),
                MaxLeverageRule(max_leverage=leverage),
            ]
        )

        engine = BacktestEngine(
            strategy=strategy,
            store=store,
            initial_capital=Decimal(str(capital)),
            market_type=market,
            leverage=leverage,
            risk_manager=risk_manager,
        )

        with st.spinner(f"Running {strategy_name} on {symbol}..."):
            result = asyncio.run(
                engine.run(
                    symbols=[symbol],
                    timeframe=timeframe,
                    start=start_dt,
                    end=end_dt,
                )
            )

        if not result.trades:
            st.warning("No trades generated. Try a different date range or strategy.")
        else:
            metrics = calculate_all(result)

            # Metrics cards
            st.subheader("Performance Metrics")
            c1, c2, c3, c4, c5 = st.columns(5)
            ret_val = metrics["total_return_pct"]
            c1.metric(
                "Total Return", f"{ret_val:+.2f}%", delta=f"{ret_val:+.2f}%", delta_color="normal"
            )
            c2.metric("Sharpe Ratio", f"{metrics['sharpe_ratio']:.3f}")
            c3.metric(
                "Max Drawdown",
                f"{metrics['max_drawdown_pct']:.2f}%",
                delta=f"-{metrics['max_drawdown_pct']:.2f}%",
                delta_color="inverse",
            )
            c4.metric("Win Rate", f"{metrics['win_rate_pct']:.1f}%")
            c5.metric("Profit Factor", f"{metrics['profit_factor']:.2f}")

            c6, c7, c8, c9, c10 = st.columns(5)
            c6.metric("Total Trades", metrics["total_trades"])
            c7.metric("Total Fees", f"${metrics['total_fees']:.2f}")
            c8.metric("Sortino", f"{metrics['sortino_ratio']:.3f}")
            c9.metric("Calmar", f"{metrics['calmar_ratio']:.3f}")
            c10.metric("Final Equity", f"${metrics['final_equity']:,.2f}")

            # Equity curve
            st.subheader("Equity Curve")
            if result.equity_curve:
                df_eq = pd.DataFrame(result.equity_curve, columns=["timestamp", "equity"])
                df_eq["equity"] = df_eq["equity"].astype(float)

                fig = go.Figure()
                fig.add_trace(
                    go.Scatter(
                        x=df_eq["timestamp"],
                        y=df_eq["equity"],
                        mode="lines",
                        name="Equity",
                        line=dict(color="#00ff88", width=1),
                        fill="tozeroy",
                        fillcolor="rgba(0,255,136,0.1)",
                    )
                )
                fig.add_hline(
                    y=float(capital), line_dash="dash", line_color="gray", annotation_text="Initial"
                )
                fig.update_layout(
                    height=400,
                    margin=dict(l=0, r=0, t=0, b=0),
                    xaxis_title=None,
                    yaxis_title="Equity ($)",
                    template="plotly_dark",
                )
                st.plotly_chart(fig, use_container_width=True)

            # Trades table
            st.subheader(f"Trades ({len(result.trades)} total)")

            trade_rows = []
            for t in result.trades:
                trade_rows.append(
                    {
                        "Entry": t.entry_time.strftime("%m-%d %H:%M") if t.entry_time else "-",
                        "Exit": t.exit_time.strftime("%m-%d %H:%M") if t.exit_time else "-",
                        "Side": t.side.value.upper(),
                        "Entry $": f"{float(t.entry_price):.2f}",
                        "Exit $": f"{float(t.exit_price or 0):.2f}",
                        "P&L $": f"{float(t.pnl):+.2f}",
                        "P&L %": f"{float(t.pnl_pct):+.2f}",
                        "Fee $": f"{float(t.fee):.4f}",
                    }
                )

            trades_df = pd.DataFrame(trade_rows)
            st.dataframe(trades_df, use_container_width=True, hide_index=True, height=400)
