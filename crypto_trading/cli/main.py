import asyncio
import subprocess
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import typer
from rich.console import Console

from crypto_trading.backtest.engine import BacktestEngine
from crypto_trading.backtest.reporter import print_equity_chart, print_result
from crypto_trading.config.settings import load_settings
from crypto_trading.data.fetcher import HistoricalDataFetcher
from crypto_trading.data.store import ParquetStore
from crypto_trading.exchanges.binance import BinanceExchange
from crypto_trading.exchanges.binance_ws import BinanceWebSocket
from crypto_trading.execution.live_broker import LiveBroker
from crypto_trading.execution.paper_broker import PaperBroker
from crypto_trading.live.runner import LiveTradingRunner
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
    proxy: str | None = typer.Option(None, "--proxy", help="HTTP proxy, e.g. http://127.0.0.1:7890"),
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


async def _paper(
    strategy_name: str,
    symbols: list[str],
    timeframes: list[str],
    capital: str,
    market: str,
    leverage: int,
    proxy: str | None,
    config_path: str | None,
) -> None:
    settings = load_settings(config_path)
    strategy_params = settings.strategy_params.get(strategy_name, {})

    strategy = get_strategy(name=strategy_name, symbols=symbols, params=strategy_params)
    store = ParquetStore(base_dir=settings.data.parquet_dir)

    broker = PaperBroker(
        market_type=market,
        leverage=leverage,
    )

    ws_client = BinanceWebSocket(
        symbols=symbols,
        timeframes=timeframes,
        market_type=market,
        proxy=proxy or settings.exchange.proxy,
    )

    risk = settings.risk
    risk_manager = RiskManager([
        MaxDrawdownRule(max_drawdown_pct=risk.max_drawdown_pct),
        PositionSizeRule(max_position_pct=risk.max_position_pct),
        MaxOpenPositionsRule(max_positions=risk.max_open_positions),
        MinConfidenceRule(min_confidence=risk.min_confidence),
        MaxLeverageRule(max_leverage=risk.max_leverage),
    ])

    runner = LiveTradingRunner(
        strategy=strategy,
        broker=broker,
        ws_client=ws_client,
        store=store,
        risk_manager=risk_manager,
        initial_capital=Decimal(capital),
    )

    console.print(f"[bold]Paper trading: {strategy_name} on {symbols} ({timeframes})[/bold]")
    console.print(f"Market: {market}, Leverage: {leverage}x, Capital: ${capital}")
    console.print("Press Ctrl+C to stop")
    console.print()

    await runner.run()


@app.command()
def paper(
    strategy: str = typer.Option(..., "--strategy", help="Strategy name"),
    symbols: list[str] = typer.Option(
        ..., "--symbols", "-s", help="Trading pairs, comma-separated"
    ),
    timeframes: list[str] = typer.Option(["1h"], "--timeframes", "-t", help="Candle timeframes"),
    capital: str = typer.Option("10000", "--capital", help="Initial capital in USDT"),
    market: str = typer.Option("futures", "--market", "-m", help="Market type: spot or futures"),
    leverage: int = typer.Option(1, "--leverage", "-l", help="Leverage (futures only)"),
    proxy: str | None = typer.Option(None, "--proxy", help="HTTP proxy"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
) -> None:
    """Run paper trading with simulated fills against real-time data."""
    asyncio.run(
        _paper(strategy, symbols, timeframes, capital, market, leverage, proxy, config)
    )


async def _live(
    strategy_name: str,
    symbols: list[str],
    timeframes: list[str],
    capital: str,
    market: str,
    leverage: int,
    proxy: str | None,
    config_path: str | None,
) -> None:
    settings = load_settings(config_path)

    if not settings.exchange.api_key:
        console.print("[red]API key not configured. Set BINANCE_API_KEY in .env[/red]")
        return

    strategy_params = settings.strategy_params.get(strategy_name, {})
    strategy = get_strategy(name=strategy_name, symbols=symbols, params=strategy_params)
    store = ParquetStore(base_dir=settings.data.parquet_dir)

    exchange = BinanceExchange(
        api_key=settings.exchange.api_key,
        secret_key=settings.exchange.secret_key,
        market_type=market,
        testnet=settings.exchange.testnet,
        proxy=proxy or settings.exchange.proxy,
    )

    broker = LiveBroker(
        exchange=exchange,
        mode="testnet" if settings.exchange.testnet else "live",
    )

    ws_client = BinanceWebSocket(
        symbols=symbols,
        timeframes=timeframes,
        market_type=market,
        proxy=proxy or settings.exchange.proxy,
    )

    risk = settings.risk
    risk_manager = RiskManager([
        MaxDrawdownRule(max_drawdown_pct=risk.max_drawdown_pct),
        PositionSizeRule(max_position_pct=risk.max_position_pct),
        MaxOpenPositionsRule(max_positions=risk.max_open_positions),
        MinConfidenceRule(min_confidence=risk.min_confidence),
        MaxLeverageRule(max_leverage=risk.max_leverage),
    ])

    runner = LiveTradingRunner(
        strategy=strategy,
        broker=broker,
        ws_client=ws_client,
        store=store,
        risk_manager=risk_manager,
        initial_capital=Decimal(capital),
    )

    console.print(f"[bold red]LIVE trading: {strategy_name} on {symbols}[/bold red]")
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
    symbols: list[str] = typer.Option(
        ..., "--symbols", "-s", help="Trading pairs, comma-separated"
    ),
    timeframes: list[str] = typer.Option(["1h"], "--timeframes", "-t", help="Candle timeframes"),
    capital: str = typer.Option("10000", "--capital", help="Initial capital in USDT"),
    market: str = typer.Option("futures", "--market", "-m", help="Market type: spot or futures"),
    leverage: int = typer.Option(1, "--leverage", "-l", help="Leverage (futures only)"),
    proxy: str | None = typer.Option(None, "--proxy", help="HTTP proxy"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
) -> None:
    """Run live trading with real orders on the exchange."""
    asyncio.run(
        _live(strategy, symbols, timeframes, capital, market, leverage, proxy, config)
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
    subprocess.run([
        "streamlit", "run", str(app_path),
        "--server.port", str(port),
        "--server.address", host,
        "--theme.dark", "true",
    ])


if __name__ == "__main__":
    app()
