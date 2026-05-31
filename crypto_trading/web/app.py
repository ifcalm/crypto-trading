"""Crypto Trading — Streamlit UI."""

import pandas as pd
import streamlit as st

from crypto_trading.config.settings import load_settings
from crypto_trading.data.store import ParquetStore
from crypto_trading.strategies import list_strategies

st.set_page_config(
    page_title="Crypto Trading",
    page_icon="📈",
    layout="wide",
)

st.title("Crypto Trading System")
st.markdown("量化交易 — 币安现货+合约")

settings = load_settings()
store = ParquetStore(base_dir=settings.data.parquet_dir)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Market", settings.market_type.upper())
col2.metric("Symbols", ", ".join(settings.trading.symbols))
col3.metric("Timeframes", ", ".join(settings.trading.timeframes))
col4.metric("Strategies", str(len(list_strategies())))


st.subheader("Available Data")

table_data = []
for sym in settings.trading.symbols:
    for tf in settings.trading.timeframes:
        dr = store.get_date_range(sym, tf)
        if dr:
            table_data.append(
                {
                    "Symbol": sym,
                    "Timeframe": tf,
                    "Start": dr[0].strftime("%Y-%m-%d"),
                    "End": dr[1].strftime("%Y-%m-%d"),
                    "Days": (dr[1] - dr[0]).days,
                }
            )

if table_data:
    df = pd.DataFrame(table_data)
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("No data yet. Go to the Data page to download historical data.")


st.subheader("Strategies")

strategy_names = list_strategies()
cols = st.columns(max(1, len(strategy_names)))
for i, name in enumerate(strategy_names):
    with cols[i]:
        with st.container(border=True):
            params = settings.strategy_params.get(name, {})
            st.markdown(f"**{name}**")
            for k, v in params.items():
                st.caption(f"{k}: {v}")


st.divider()
st.caption("Use the sidebar navigation to run backtests and manage data.")
