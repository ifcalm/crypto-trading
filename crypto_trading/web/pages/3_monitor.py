"""Live Monitor page — real-time paper/live trading status."""

import json
import time
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

STATUS_FILE = Path("data/status.json")

st.set_page_config(page_title="Live Monitor", page_icon="🔴", layout="wide")
st.title("Live Trading Monitor")

with st.sidebar:
    refresh_rate = st.slider("Refresh (seconds)", 1, 10, 2)
    auto_refresh = st.checkbox("Auto-refresh", value=True)
    st.divider()
    st.caption("Run `crypto-trading paper` or `live` in another terminal.")
    st.caption("This page reads from `data/status.json`.")

if not STATUS_FILE.exists():
    st.info(
        "No trading status found. "
        "Start paper/live trading in another terminal:\\n\\n"
        "```bash\\n"
        "crypto-trading paper --strategy ma_crossover --symbols BTC/USDT\\n"
        "```"
    )
    if auto_refresh:
        time.sleep(refresh_rate)
        st.rerun()
    st.stop()

try:
    with open(STATUS_FILE) as f:
        status = json.load(f)
except (json.JSONDecodeError, OSError):
    st.error("Failed to read status file.")
    st.stop()

running = status.get("running", False)

# Header
c0, c1 = st.columns([3, 1])
c0.subheader("Trading Active" if running else "Trading Stopped")
badge = "🟢 Running" if running else "⏹️ Stopped"
c1.markdown(f"### {badge}")

# Metrics
m1, m2, m3, m4, m5 = st.columns(5)
pnl = status.get("pnl", 0)
pnl_pct = status.get("pnl_pct", 0)
m1.metric("Equity", f"${status.get('equity', 0):,.2f}")
m2.metric("P&L", f"${pnl:+,.2f}", delta=f"{pnl_pct:+.2f}%" if pnl_pct else None)
m3.metric("Bars", status.get("bar_count", 0))
m4.metric("Drawdown", f"{status.get('drawdown', 0) * 100:.2f}%")
m5.metric("Positions", len(status.get("positions", [])))

if status.get("started_at"):
    st.caption(f"Started: {status['started_at']}  |  Last update: just now")

st.divider()

# Charts and tables
left, right = st.columns([3, 2])

with left:
    st.subheader("Equity Curve")
    equity_history = status.get("equity_history", [])
    if equity_history:
        df_eq = pd.DataFrame(equity_history)
        df_eq["time"] = pd.to_datetime(df_eq["time"])
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=df_eq["time"],
                y=df_eq["equity"],
                mode="lines",
                name="Equity",
                line=dict(color="#00ff88", width=2),
                fill="tozeroy",
                fillcolor="rgba(0,255,136,0.1)",
            )
        )
        initial = status.get("initial_capital", 0)
        if initial:
            fig.add_hline(y=initial, line_dash="dash", line_color="gray", annotation_text="Initial")
        fig.update_layout(
            height=350,
            margin=dict(l=0, r=0, t=0, b=0),
            template="plotly_dark",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Collecting data...")

with right:
    st.subheader("Open Positions")
    positions = status.get("positions", [])
    if positions:
        rows = [
            {
                "Symbol": p["symbol"],
                "Side": p["side"].upper(),
                "Qty": f"{p['quantity']:.4f}",
                "Entry": f"${p['entry_price']:.2f}",
                "Mark": f"${p['mark_price']:.2f}",
                "PnL": f"${p['unrealized_pnl']:+.2f}",
                "Lev": f"{p['leverage']}x",
            }
            for p in positions
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=300)
    else:
        st.info("No open positions")

# Recent trades
st.subheader("Recent Trades")
trades = status.get("recent_trades", [])
if trades:
    rows = [
        {
            "Time": t.get("time", "")[:19] if t.get("time") else "",
            "Symbol": t.get("symbol", ""),
            "Side": t.get("side", ""),
            "Qty": f"{t.get('qty', 0):.4f}",
            "Price": f"${t.get('price', 0):.2f}",
        }
        for t in reversed(trades[-30:])
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=400)
else:
    st.info("No trades yet")

# Auto-refresh
if auto_refresh:
    time.sleep(refresh_rate)
    st.rerun()
