import asyncio
import os
import subprocess
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from crypto_trading.backtest.engine import BacktestEngine
from crypto_trading.backtest.reporter import print_equity_chart, print_result
from crypto_trading.config.settings import Settings, load_settings
from crypto_trading.core.exchange import Exchange
from crypto_trading.core.strategy import Strategy
from crypto_trading.data.fetcher import HistoricalDataFetcher
from crypto_trading.data.store import ParquetStore
from crypto_trading.exchanges.binance import BinanceExchange
from crypto_trading.exchanges.binance_ws import BinanceWebSocket
from crypto_trading.exchanges.binance_ws_depth import DepthWebSocket
from crypto_trading.exchanges.hyperliquid import HyperliquidExchange
from crypto_trading.exchanges.hyperliquid_ws import HyperliquidWebSocket
from crypto_trading.execution.live_broker import LiveBroker
from crypto_trading.execution.paper_broker import PaperBroker
from crypto_trading.live.engine import EventDrivenRunner as LiveTradingRunner
from crypto_trading.risk.manager import RiskManager
from crypto_trading.risk.rules import (
    MaxDrawdownRule,
    MaxLeverageRule,
    MaxOpenPositionsRule,
    MinConfidenceRule,
    PositionSizeRule,
)
from crypto_trading.strategies import get_strategy, list_strategies

app = typer.Typer(name="crypto-trading")
console = Console()


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        console.print("Usage: crypto-trading [COMMAND]")
        console.print("Commands: fetch, backtest, paper, live, strategies, ui")
        console.print("Run 'crypto-trading COMMAND --help' for details.")


async def _fetch(
    symbol: str,
    timeframe: str,
    since: str | None,
    until: str | None,
    proxy: str | None,
    config: str | None,
) -> None:
    settings = load_settings(config)

    since_dt: datetime | None = None
    if since:
        since_dt = datetime.fromisoformat(since)
    until_dt: datetime | None = None
    if until:
        until_dt = datetime.fromisoformat(until)

    exchange = BinanceExchange(
        api_key=settings.exchange.api_key,
        secret_key=settings.exchange.secret_key,
        market_type=settings.market_type,
        testnet=settings.exchange.testnet,
        proxy=proxy or settings.exchange.proxy,
    )
    store = ParquetStore(base_dir=settings.data.parquet_dir)

    try:
        fetcher = HistoricalDataFetcher(exchange, store)
        count = await fetcher.fetch_and_store(
            symbol=symbol,
            timeframe=timeframe,
            since=since_dt,
            until=until_dt,
        )

        if count > 0:
            console.print(
                f"[green]Fetched and stored {count} bars for {symbol} ({timeframe})[/green]"
            )
        else:
            console.print(f"[yellow]No new data for {symbol} ({timeframe})[/yellow]")

        date_range = store.get_date_range(symbol, timeframe)
        if date_range:
            console.print(f"Date range: {date_range[0]} -> {date_range[1]}")
    finally:
        await exchange.close()


@app.command()
def fetch(
    symbol: str = typer.Option(..., "--symbol", "-s", help="Trading pair, e.g. BTC/USDT"),
    timeframe: str = typer.Option(
        "1h", "--timeframe", "-t", help="Candle timeframe: 1m, 5m, 15m, 1h, 4h, 1d"
    ),
    since: str | None = typer.Option(None, "--since", help="Start date (ISO format)"),
    until: str | None = typer.Option(None, "--until", help="End date (ISO format)"),
    proxy: str | None = typer.Option(
        None, "--proxy", help="HTTP proxy, e.g. http://127.0.0.1:7890"
    ),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
) -> None:
    """Download historical OHLCV data from the exchange and store it."""
    asyncio.run(_fetch(symbol, timeframe, since, until, proxy, config))


async def _backtest(
    strategy_name: str,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    capital: str,
    market: str,
    leverage: int,
    config_path: str | None,
) -> None:
    settings = load_settings(config_path)
    strategy_params = settings.strategy_params.get(strategy_name, {})

    strategy = get_strategy(
        name=strategy_name,
        symbols=[symbol],
        params=strategy_params,
    )

    store = ParquetStore(base_dir=settings.data.parquet_dir)

    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)

    engine = BacktestEngine(
        strategy=strategy,
        store=store,
        initial_capital=Decimal(capital),
        market_type=market,
        leverage=leverage,
    )

    console.print(f"[bold]Running backtest: {strategy_name} on {symbol} ({timeframe})[/bold]")
    console.print(f"Period: {start_dt} -> {end_dt}")
    console.print(f"Market: {market}, Leverage: {leverage}x, Capital: ${capital}")
    console.print()

    result = await engine.run(
        symbols=[symbol],
        timeframe=timeframe,
        start=start_dt,
        end=end_dt,
    )

    print_result(result)

    if result.equity_curve:
        print_equity_chart([(ts, float(v)) for ts, v in result.equity_curve])


@app.command()
def backtest(
    strategy: str = typer.Option(..., "--strategy", help="Strategy name"),
    symbol: str = typer.Option(..., "--symbol", "-s", help="Trading pair"),
    timeframe: str = typer.Option("1h", "--timeframe", "-t", help="Candle timeframe"),
    start: str = typer.Option(..., "--start", help="Start date (ISO format)"),
    end: str = typer.Option(..., "--end", help="End date (ISO format)"),
    capital: str = typer.Option("10000", "--capital", help="Initial capital in USDT"),
    market: str = typer.Option("futures", "--market", "-m", help="Market type: spot or futures"),
    leverage: int = typer.Option(1, "--leverage", "-l", help="Leverage (futures only)"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
) -> None:
    """Run a strategy backtest against historical data."""
    asyncio.run(
        _backtest(strategy, symbol, timeframe, start, end, capital, market, leverage, config)
    )


def _normalize_symbols(raw: list[str] | None) -> list[str] | None:
    """Split comma-separated symbols like ['BTC/USDT,ETH/USDT'] -> ['BTC/USDT', 'ETH/USDT']."""
    if raw is None:
        return None
    result: list[str] = []
    for s in raw:
        result.extend(part.strip() for part in s.split(",") if part.strip())
    return result or None


async def _resolve_symbols(
    symbols: list[str] | None,
    settings: Settings,
    strategy: Strategy,
    proxy: str,
    market: str,
) -> list[str]:
    """Resolve symbols: use explicit list or auto-screen from exchange."""
    if symbols:
        symbols = _normalize_symbols(symbols)
    if symbols:
        return symbols

    scr = settings.screener
    if not scr.enabled:
        symbols = settings.trading.symbols
        console.print(f"[yellow]Screener disabled, using config symbols: {symbols}[/yellow]")
        return symbols

    console.print("[bold]Auto-screening symbols from exchange...[/bold]")

    exchange = BinanceExchange(
        market_type=market,
        proxy=proxy,
    )

    try:
        tickers = await exchange.fetch_tickers()
        console.print(f"  Fetched {len(tickers)} tickers from exchange")

        from crypto_trading.screener import (
            MaxSymbolsFilter,
            MinVolumeFilter,
            QuoteCurrencyFilter,
            SymbolScreener,
        )

        screener = SymbolScreener(
            [
                QuoteCurrencyFilter(quote=scr.quote_currency),
                MinVolumeFilter(min_volume_usdt=scr.min_volume_usdt),
                MaxSymbolsFilter(max_symbols=scr.max_symbols),
            ]
        )
        tickers = [t for t in tickers if t.symbol in screener.apply(tickers)]
        console.print(f"  After screener: {len(tickers)} symbols")

        resolved = await strategy.select_symbols(tickers)
        # Normalize futures symbols: "TOSHI/USDT:USDT" -> "TOSHI/USDT"
        resolved = [s.split(":")[0] if ":" in s else s for s in resolved]
        console.print(f"  After strategy filter: {len(resolved)} symbols")

        if not resolved:
            console.print("[red]No symbols matched. Falling back to config symbols.[/red]")
            return settings.trading.symbols

        console.print(f"[green]Selected symbols: {resolved}[/green]")
        return resolved
    finally:
        await exchange.close()


def _needs_depth_ws(strategy_name: str) -> bool:
    """Check if a strategy needs orderbook depth data."""
    return strategy_name.startswith("llm_orderbook")


def _create_exchange_and_ws(
    exchange_name: str,
    settings: Settings,
    market: str,
    proxy: str | None,
    symbols: list[str],
    timeframes: list[str],
) -> dict[str, Any]:
    """Create exchange, broker, and WebSocket instances for the given exchange."""
    result: dict[str, Any] = {"exchange": None, "broker": None, "ws_client": None, "depth_ws": None}

    if exchange_name == "binance":
        exchange: Exchange = BinanceExchange(
            api_key=settings.exchange.api_key,
            secret_key=settings.exchange.secret_key,
            market_type=market,
            testnet=settings.exchange.testnet,
            proxy=proxy or settings.exchange.proxy,
        )
        ws_client: Any = BinanceWebSocket(
            symbols=symbols,
            timeframes=timeframes,
            market_type=market,
            proxy=proxy or settings.exchange.proxy,
        )
        result["broker"] = LiveBroker(
            exchange=exchange,
            mode="testnet" if settings.exchange.testnet else "live",
        )
        result["ws_client"] = ws_client
        result["exchange"] = exchange

    elif exchange_name == "hyperliquid":
        if not settings.hyperliquid.private_key and not os.environ.get("HYPERLIQUID_PRIVATE_KEY"):
            raise ValueError("HYPERLIQUID_PRIVATE_KEY not set")

        exchange = HyperliquidExchange(
            private_key=settings.hyperliquid.private_key,
            wallet_address=settings.hyperliquid.wallet_address,
            market_type=market,
            testnet=settings.hyperliquid.testnet,
            vault_address=settings.hyperliquid.vault_address or None,
        )
        ws_client = HyperliquidWebSocket(
            symbols=symbols,
            timeframes=timeframes,
            market_type=market,
            testnet=settings.hyperliquid.testnet,
        )
        result["broker"] = LiveBroker(
            exchange=exchange,
            mode="testnet" if settings.hyperliquid.testnet else "live",
        )
        result["ws_client"] = ws_client
        result["exchange"] = exchange

    # Depth WS (Binance only for now)
    if exchange_name == "binance":
        result["depth_ws"] = DepthWebSocket(
            symbols=symbols,
            market_type=market,
            proxy=proxy or settings.exchange.proxy,
        )

    return result


async def _paper(
    strategy_name: str,
    symbols: list[str] | None,
    timeframes: list[str],
    capital: str,
    market: str,
    leverage: int,
    proxy: str | None,
    config_path: str | None,
    exchange_name: str = "binance",
) -> None:
    settings = load_settings(config_path)
    strategy_params = settings.strategy_params.get(strategy_name, {})

    resolved_symbols = await _resolve_symbols(
        symbols,
        settings,
        get_strategy(name=strategy_name, symbols=[], params=strategy_params),
        proxy or settings.exchange.proxy,
        market,
    )

    strategy = get_strategy(name=strategy_name, symbols=resolved_symbols, params=strategy_params)
    store = ParquetStore(base_dir=settings.data.parquet_dir)

    broker = PaperBroker(
        market_type=market,
        leverage=leverage,
    )

    components = _create_exchange_and_ws(
        exchange_name, settings, market, proxy, resolved_symbols, timeframes
    )
    ws_client = components["ws_client"]
    depth_ws = components["depth_ws"] if _needs_depth_ws(strategy_name) else None

    if _needs_depth_ws(strategy_name) and exchange_name != "binance":
        console.print(
            "[yellow]Depth data only available on Binance — LLM strategy disabled[/yellow]"
        )

    risk = settings.risk
    risk_manager = RiskManager(
        [
            MaxDrawdownRule(max_drawdown_pct=risk.max_drawdown_pct),
            PositionSizeRule(max_position_pct=risk.max_position_pct),
            MaxOpenPositionsRule(max_positions=risk.max_open_positions),
            MinConfidenceRule(min_confidence=risk.min_confidence),
            MaxLeverageRule(max_leverage=risk.max_leverage),
        ]
    )

    runner = LiveTradingRunner(
        strategy=strategy,
        broker=broker,
        ws_client=ws_client,
        store=store,
        risk_manager=risk_manager,
        initial_capital=Decimal(capital),
        depth_ws=depth_ws,
        db_url=settings.data.database_url,
        market_type=market,
    )

    msg = f"[bold]Paper trading ({exchange_name}): {strategy_name} on {resolved_symbols}[/bold]"
    console.print(msg)
    console.print(f"Market: {market}, Leverage: {leverage}x, Capital: ${capital}")
    console.print("Press Ctrl+C to stop")
    console.print()

    await runner.run()


@app.command()
def paper(
    strategy: str = typer.Option(..., "--strategy", help="Strategy name"),
    symbols: list[str] | None = typer.Option(
        None, "--symbols", "-s", help="Trading pairs, comma-separated. Auto-screened if omitted."
    ),
    timeframes: list[str] = typer.Option(["1h"], "--timeframes", "-t", help="Candle timeframes"),
    capital: str = typer.Option("10000", "--capital", help="Initial capital in USDT"),
    market: str = typer.Option("futures", "--market", "-m", help="Market type: spot or futures"),
    leverage: int = typer.Option(1, "--leverage", "-l", help="Leverage (futures only)"),
    proxy: str | None = typer.Option(None, "--proxy", help="HTTP proxy"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
    exchange: str = typer.Option(
        "binance", "--exchange", "-e", help="Exchange: binance or hyperliquid"
    ),
) -> None:
    """Run paper trading with simulated fills against real-time data."""
    asyncio.run(
        _paper(strategy, symbols, timeframes, capital, market, leverage, proxy, config, exchange)
    )


async def _live(
    strategy_name: str,
    symbols: list[str] | None,
    timeframes: list[str],
    capital: str,
    market: str,
    leverage: int,
    proxy: str | None,
    config_path: str | None,
    exchange_name: str = "binance",
) -> None:
    settings = load_settings(config_path)

    if exchange_name == "binance" and not settings.exchange.api_key:
        console.print("[red]BINANCE_API_KEY not configured in .env[/red]")
        return

    dummy_strategy = get_strategy(
        name=strategy_name,
        symbols=[],
        params=settings.strategy_params.get(strategy_name, {}),
    )
    resolved_symbols = await _resolve_symbols(
        symbols,
        settings,
        dummy_strategy,
        proxy or settings.exchange.proxy,
        market,
    )

    strategy_params = settings.strategy_params.get(strategy_name, {})
    strategy = get_strategy(name=strategy_name, symbols=resolved_symbols, params=strategy_params)
    store = ParquetStore(base_dir=settings.data.parquet_dir)

    components = _create_exchange_and_ws(
        exchange_name, settings, market, proxy, resolved_symbols, timeframes
    )
    exchange = components["exchange"]
    broker = components["broker"]
    ws_client = components["ws_client"]
    depth_ws = components["depth_ws"] if _needs_depth_ws(strategy_name) else None

    if _needs_depth_ws(strategy_name) and exchange_name != "binance":
        console.print("[yellow]Depth data only available on Binance[/yellow]")

    risk = settings.risk
    risk_manager = RiskManager(
        [
            MaxDrawdownRule(max_drawdown_pct=risk.max_drawdown_pct),
            PositionSizeRule(max_position_pct=risk.max_position_pct),
            MaxOpenPositionsRule(max_positions=risk.max_open_positions),
            MinConfidenceRule(min_confidence=risk.min_confidence),
            MaxLeverageRule(max_leverage=risk.max_leverage),
        ]
    )

    runner = LiveTradingRunner(
        strategy=strategy,
        broker=broker,
        ws_client=ws_client,
        store=store,
        risk_manager=risk_manager,
        initial_capital=Decimal(capital),
        depth_ws=depth_ws,
        db_url=settings.data.database_url,
        market_type=market,
    )

    exchange_type = (
        "TESTNET" if settings.exchange.testnet or settings.hyperliquid.testnet else "LIVE"
    )
    info_line = (
        f"[bold red]{exchange_type} ({exchange_name}): "
        f"{strategy_name} on {resolved_symbols}[/bold red]"
    )
    console.print(info_line)
    console.print(f"Market: {market}, Leverage: {leverage}x")
    if settings.exchange.testnet:
        console.print("[yellow]Running on TESTNET[/yellow]")
    else:
        console.print("[bold red]REAL MONEY — Binance Live[/bold red]")
    console.print("Press Ctrl+C to stop")
    console.print()

    await runner.run()
    await exchange.close()


@app.command()
def live(
    strategy: str = typer.Option(..., "--strategy", help="Strategy name"),
    symbols: list[str] | None = typer.Option(
        None, "--symbols", "-s", help="Trading pairs, comma-separated. Auto-screened if omitted."
    ),
    timeframes: list[str] = typer.Option(["1h"], "--timeframes", "-t", help="Candle timeframes"),
    capital: str = typer.Option("10000", "--capital", help="Initial capital in USDT"),
    market: str = typer.Option("futures", "--market", "-m", help="Market type: spot or futures"),
    leverage: int = typer.Option(1, "--leverage", "-l", help="Leverage (futures only)"),
    proxy: str | None = typer.Option(None, "--proxy", help="HTTP proxy"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
    exchange: str = typer.Option(
        "binance", "--exchange", "-e", help="Exchange: binance or hyperliquid"
    ),
) -> None:
    """Run live trading with real orders on the exchange."""
    asyncio.run(
        _live(strategy, symbols, timeframes, capital, market, leverage, proxy, config, exchange)
    )


@app.command()
def strategies() -> None:
    """List available strategies."""
    console.print("[bold]Available strategies:[/bold]")
    for name in list_strategies():
        console.print(f"  - {name}")


@app.command()
def ui(
    port: int = typer.Option(8501, "--port", "-p", help="Web server port"),
    host: str = typer.Option("localhost", "--host", help="Web server host"),
) -> None:
    """Launch the Streamlit web UI."""
    app_path = Path(__file__).parent.parent / "web" / "app.py"
    console.print(f"[green]Starting Streamlit UI at http://{host}:{port}[/green]")
    subprocess.run(
        [
            "streamlit",
            "run",
            str(app_path),
            "--server.port",
            str(port),
            "--server.address",
            host,
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
            "--theme.base",
            "dark",
        ]
    )


async def _funding_arb(
    symbol: str,
    notional: str,
    min_rate: str,
    exit_rate: str,
    check_interval: int,
    leverage: int,
    proxy: str | None,
    config_path: str | None,
) -> None:
    settings = load_settings(config_path)
    from crypto_trading.strategies.funding_rate_arb import FundingRateArbitrage

    spot_ex = BinanceExchange(
        api_key=settings.exchange.api_key,
        secret_key=settings.exchange.secret_key,
        market_type="spot",
        testnet=settings.exchange.testnet,
        proxy=proxy or settings.exchange.proxy,
    )
    futures_ex = BinanceExchange(
        api_key=settings.exchange.api_key,
        secret_key=settings.exchange.secret_key,
        market_type=settings.market_type,
        testnet=settings.exchange.testnet,
        proxy=proxy or settings.exchange.proxy,
    )

    arb = FundingRateArbitrage(
        spot_exchange=spot_ex,
        futures_exchange=futures_ex,
        symbol=symbol,
        notional=Decimal(notional),
        min_funding_rate=Decimal(min_rate),
        exit_funding_rate=Decimal(exit_rate),
        check_interval=check_interval,
        leverage=leverage,
    )

    console.print(f"[bold]Funding Rate Arb: {symbol}[/bold]")
    console.print(f"Notional: ${notional}, Min rate: {min_rate}, Exit rate: {exit_rate}")
    console.print("Press Ctrl+C to stop")
    console.print()

    try:
        await arb.run()
    except asyncio.CancelledError:
        pass
    finally:
        await arb.stop()
        await spot_ex.close()
        await futures_ex.close()


@app.command()
def funding_arb(
    symbol: str = typer.Option("BTC/USDT", "--symbol", "-s", help="Trading pair"),
    notional: str = typer.Option("1000", "--notional", "-n", help="Position notional in USDT"),
    min_rate: str = typer.Option("0.0001", "--min-rate", help="Min funding rate to open (0.01%)"),
    exit_rate: str = typer.Option("0", "--exit-rate", help="Exit when rate ≤ this value"),
    check_interval: int = typer.Option(300, "--interval", "-i", help="Check interval in seconds"),
    leverage: int = typer.Option(1, "--leverage", "-l", help="Leverage"),
    proxy: str | None = typer.Option(None, "--proxy", help="HTTP proxy"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
) -> None:
    """Run delta-neutral funding rate arbitrage."""
    asyncio.run(
        _funding_arb(symbol, notional, min_rate, exit_rate, check_interval, leverage, proxy, config)
    )


async def _market_make(
    symbol: str,
    quote_size: str,
    spread_bps: float,
    max_spread_bps: float,
    cycle_interval: float,
    max_pos: int,
    leverage: int,
    proxy: str | None,
    config_path: str | None,
) -> None:
    settings = load_settings(config_path)

    exchange = BinanceExchange(
        api_key=settings.exchange.api_key,
        secret_key=settings.exchange.secret_key,
        market_type=settings.market_type,
        testnet=settings.exchange.testnet,
        proxy=proxy or settings.exchange.proxy,
    )

    from crypto_trading.strategies.market_maker import MarketMaker

    mm = MarketMaker(
        exchange=exchange,
        symbol=symbol,
        quote_size=Decimal(quote_size),
        base_spread_bps=spread_bps,
        max_spread_bps=max_spread_bps,
        cycle_interval=cycle_interval,
        position_limits=(-max_pos, max_pos),
        leverage=leverage,
    )

    console.print(f"[bold]Market Making: {symbol}[/bold]")
    console.print(
        f"Quote: {quote_size}, Spread: {spread_bps}bps, "
        f"Max pos: ±{max_pos}, Cycle: {cycle_interval}s"
    )
    console.print("Press Ctrl+C to stop")
    console.print()

    try:
        await mm.run()
    except asyncio.CancelledError:
        pass
    finally:
        await mm.stop()
        await exchange.close()


@app.command()
def market_make(
    symbol: str = typer.Option("BTC/USDT", "--symbol", "-s", help="Trading pair"),
    quote_size: str = typer.Option("0.001", "--size", help="Quote size per order"),
    spread_bps: float = typer.Option(5.0, "--spread", help="Half-spread in basis points (0.05%)"),
    max_spread_bps: float = typer.Option(
        50.0, "--max-spread", help="Max half-spread when inventory skewed"
    ),
    cycle_interval: float = typer.Option(2.0, "--cycle", help="Order refresh interval in seconds"),
    max_pos: int = typer.Option(5, "--max-pos", help="Max net position (±) before pausing"),
    leverage: int = typer.Option(1, "--leverage", "-l", help="Leverage"),
    proxy: str | None = typer.Option(None, "--proxy", help="HTTP proxy"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
) -> None:
    """Run a simple inventory-aware market maker."""
    asyncio.run(
        _market_make(
            symbol,
            quote_size,
            spread_bps,
            max_spread_bps,
            cycle_interval,
            max_pos,
            leverage,
            proxy,
            config,
        )
    )


if __name__ == "__main__":
    app()
