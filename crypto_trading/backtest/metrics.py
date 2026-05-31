import math
from datetime import datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from crypto_trading.backtest.engine import BacktestResult, Trade


def _to_series(equity_curve: list[tuple[datetime, Decimal]]) -> pd.Series:
    if not equity_curve:
        return pd.Series(dtype=float)
    df = pd.DataFrame(equity_curve, columns=["timestamp", "equity"])
    df["equity"] = df["equity"].astype(float)
    return df.set_index("timestamp")["equity"]


def _daily_returns(equity: pd.Series) -> pd.Series:
    if len(equity) < 2:
        return pd.Series(dtype=float)
    daily = equity.resample("D").last().dropna()
    return daily.pct_change().dropna()


def calculate_total_return(result: BacktestResult) -> float:
    if result.initial_capital == 0:
        return 0.0
    return float(result.total_return_pct)


def calculate_max_drawdown(
    equity_curve: list[tuple[datetime, Decimal]],
) -> tuple[float, datetime | None, datetime | None]:
    if not equity_curve:
        return 0.0, None, None

    equity = _to_series(equity_curve)
    peak = equity.expanding().max()
    drawdown = (equity - peak) / peak
    max_dd = drawdown.min()
    max_dd_end = drawdown.idxmin()

    peak_before_dd = equity[:max_dd_end]
    if len(peak_before_dd) > 0:
        max_dd_start = peak_before_dd.idxmax()
    else:
        max_dd_start = None

    return abs(float(max_dd)) * 100, max_dd_start, max_dd_end


def calculate_sharpe(
    equity_curve: list[tuple[datetime, Decimal]],
    risk_free_rate: float = 0.02,
    periods_per_year: int = 365,
) -> float:
    returns = _daily_returns(_to_series(equity_curve))
    if len(returns) < 2:
        return 0.0

    excess = returns - risk_free_rate / periods_per_year
    if excess.std() == 0:
        return 0.0
    return float(excess.mean() / excess.std() * math.sqrt(periods_per_year))


def calculate_sortino(
    equity_curve: list[tuple[datetime, Decimal]],
    risk_free_rate: float = 0.02,
    periods_per_year: int = 365,
) -> float:
    returns = _daily_returns(_to_series(equity_curve))
    if len(returns) < 2:
        return 0.0

    excess = returns - risk_free_rate / periods_per_year
    downside = excess[excess < 0]
    if len(downside) == 0 or downside.std() == 0:
        return 0.0
    return float(excess.mean() / downside.std() * math.sqrt(periods_per_year))


def calculate_calmar(
    equity_curve: list[tuple[datetime, Decimal]], periods_per_year: int = 365
) -> float:
    if not equity_curve:
        return 0.0

    total_ret = (
        float(equity_curve[-1][1] - equity_curve[0][1]) / float(equity_curve[0][1])
        if float(equity_curve[0][1]) > 0
        else 0.0
    )

    max_dd, _, _ = calculate_max_drawdown(equity_curve)
    if max_dd == 0:
        return 0.0

    years = (equity_curve[-1][0] - equity_curve[0][0]).days / 365.25
    if years == 0:
        years = 1.0

    annualized_return = (1 + total_ret) ** (1 / years) - 1
    return float(annualized_return / (max_dd / 100))


def calculate_win_rate(trades: list[Trade]) -> float:
    if not trades:
        return 0.0
    winning = sum(1 for t in trades if float(t.pnl) > 0)
    return winning / len(trades) * 100


def calculate_profit_factor(trades: list[Trade]) -> float:
    gross_profit = sum(float(t.pnl) for t in trades if float(t.pnl) > 0)
    gross_loss = abs(sum(float(t.pnl) for t in trades if float(t.pnl) < 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def calculate_all(result: BacktestResult, risk_free_rate: float = 0.02) -> dict[str, Any]:
    max_dd, dd_start, dd_end = calculate_max_drawdown(result.equity_curve)

    return {
        "total_return_pct": calculate_total_return(result),
        "sharpe_ratio": calculate_sharpe(result.equity_curve, risk_free_rate),
        "sortino_ratio": calculate_sortino(result.equity_curve, risk_free_rate),
        "calmar_ratio": calculate_calmar(result.equity_curve),
        "max_drawdown_pct": max_dd,
        "max_drawdown_start": dd_start,
        "max_drawdown_end": dd_end,
        "win_rate_pct": calculate_win_rate(result.trades),
        "profit_factor": calculate_profit_factor(result.trades),
        "total_trades": len(result.trades),
        "total_fees": float(result.total_fees),
        "initial_capital": float(result.initial_capital),
        "final_equity": float(result.final_equity),
    }
