"""Data page — download and manage historical data."""

import asyncio
from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from crypto_trading.config.settings import load_settings
from crypto_trading.data.fetcher import HistoricalDataFetcher
from crypto_trading.data.store import ParquetStore
from crypto_trading.exchanges.binance import BinanceExchange

st.set_page_config(page_title="Data Manager", page_icon="📡", layout="wide")
st.title("Data Manager")

settings = load_settings()
store = ParquetStore(base_dir=settings.data.parquet_dir)

with st.sidebar:
    st.header("Download Data")

    symbol = st.text_input("Symbol", "BTC/USDT")
    timeframe = st.selectbox("Timeframe", ["1m", "5m", "15m", "1h", "4h", "1d"],
                             index=3)

    date_range = st.date_input(
        "Date Range",
        value=(pd.Timestamp.now() - timedelta(days=30), pd.Timestamp.now()),
        max_value=pd.Timestamp.now(),
    )

    proxy = st.text_input("Proxy", settings.exchange.proxy,
                          placeholder="http://127.0.0.1:7890")

    st.divider()

    fetch_btn = st.button("Download", type="primary", use_container_width=True)

    st.divider()
    st.caption("Binance free API doesn't require a key for OHLCV data.")

tab1, tab2 = st.tabs(["Stored Data", "Chart Preview"])

with tab1:
    st.subheader("Stored OHLCV Data")

    data_info = []
    for sym in settings.trading.symbols:
        for tf in settings.trading.timeframes:
            dr = store.get_date_range(sym, tf)
            if dr:
                data_info.append({
                    "Symbol": sym,
                    "Timeframe": tf,
                    "Start": dr[0].strftime("%Y-%m-%d %H:%M"),
                    "End": dr[1].strftime("%Y-%m-%d %H:%M"),
                    "Days": f"{(dr[1] - dr[0]).days:,}",
                })

    if data_info:
        df_meta = pd.DataFrame(data_info)
        st.dataframe(df_meta, use_container_width=True, hide_index=True)
    else:
        st.info("No data stored. Use the sidebar to download.")

with tab2:
    st.subheader(f"Price Chart — {symbol} {timeframe}")

    preview_start = pd.Timestamp.now() - timedelta(days=90)
    preview_end = pd.Timestamp.now()

    bars = store.read_ohlcv(
        symbol, timeframe,
        start=preview_start.to_pydatetime(),
        end=preview_end.to_pydatetime(),
    )

    if bars:
        df = pd.DataFrame([{
            "timestamp": b.timestamp,
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
            "volume": float(b.volume),
        } for b in bars])

        fig = go.Figure()
        fig.add_trace(go.Candlestick(
            x=df["timestamp"],
            open=df["open"], high=df["high"],
            low=df["low"], close=df["close"],
            name=symbol,
            increasing_line_color="#00ff88",
            decreasing_line_color="#ff4444",
        ))
        fig.update_layout(
            height=500, margin=dict(l=0, r=0, t=0, b=0),
            xaxis_title=None, yaxis_title="Price",
            template="plotly_dark",
            xaxis_rangeslider_visible=False,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No data for preview. Download some data first.")

if fetch_btn:
    if len(date_range) != 2:
        st.error("Select a date range")
    else:
        since = date_range[0].to_pydatetime()
        until = date_range[1].to_pydatetime()

        exchange = BinanceExchange(
            market_type=settings.market_type,
            proxy=proxy,
        )
        fetcher = HistoricalDataFetcher(exchange, store)

        progress = st.progress(0, "Downloading...")
        status = st.empty()

        async def download():
            count = await fetcher.fetch_and_store(
                symbol=symbol, timeframe=timeframe,
                since=since, until=until,
            )
            return count

        try:
            count = asyncio.run(download())
            progress.progress(100)
            if count > 0:
                status.success(f"Downloaded {count} bars for {symbol} ({timeframe})")
            else:
                status.info("No new data. Already up to date.")
            asyncio.run(exchange.close())
        except Exception as e:
            progress.empty()
            st.error(f"Download failed: {e}")
            asyncio.run(exchange.close())
