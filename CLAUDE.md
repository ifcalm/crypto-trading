# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Python 加密货币量化交易系统。币安 CEX，支持现货 + 永续合约。未来扩展至 DEX（目标链待定）。

## Commands

```bash
source .venv/bin/activate   # Activate virtual environment

ruff check crypto_trading/ tests/    # Lint
ruff format crypto_trading/ tests/   # Format
mypy crypto_trading/                 # Type check
pytest                               # Run all tests
pytest tests/test_core/ -v           # Run a specific test directory
pytest -k "test_name"                # Run tests matching pattern
```

## Architecture

```
Strategy.on_bar(OHLCV) -> Signal -> RiskManager -> Broker -> Order
```

- **core/types.py** — All dataclasses and enums (OHLCV, Order, Position, Signal, Portfolio, etc.). Prices are `Decimal`, never `float`.
- **core/exchange.py** — Abstract `Exchange` interface. Every method is `async`. CEX and future DEX implementations share this contract.
- **core/strategy.py** — Abstract `Strategy` base class. Strategies implement `on_bar(symbol, bar) -> Optional[Signal]`. Same interface for backtesting and live trading.
- **config/settings.py** — pydantic-settings reading from `config.yaml` + `.env`.
- **MarketType enum** — `SPOT` vs `FUTURES`. A single `Exchange` implementation handles both by checking `market_type`.

## Key design rules

- All prices, amounts, costs use `Decimal` — no `float` in P&L paths.
- All I/O is `async` — ccxt.async_support, aiosqlite, websockets.
- Signal/Order separation: Signal = intent (from strategy), Order = execution result (from broker). RiskManager sits between them.
- OHLCV data stored as Parquet files (columnar, compressed), not in SQLite. SQLite only for trade/order metadata.
- Backtest engine fills at close price internally — the `Broker` interface is only used for paper/live trading.

## Git

- Main branch: `main`
- Run `git status` / `git diff` before committing to review changes.
