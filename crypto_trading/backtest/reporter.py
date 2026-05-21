from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from crypto_trading.backtest.engine import BacktestResult
from crypto_trading.backtest.metrics import calculate_all

console = Console()


def print_result(result: BacktestResult) -> None:
    metrics = calculate_all(result)

    summary = Table(title="Backtest Results", show_header=False)
    summary.add_column(style="bold cyan")
    summary.add_column(style="white")

    summary.add_row("Initial Capital", f"${metrics['initial_capital']:,.2f}")
    summary.add_row("Final Equity", f"${metrics['final_equity']:,.2f}")

    ret_text = f"{metrics['total_return_pct']:+.2f}%"
    ret_style = "green" if metrics["total_return_pct"] >= 0 else "red"
    summary.add_row("Total Return", f"[{ret_style}]{ret_text}[/{ret_style}]")
    summary.add_row("Total Fees", f"${metrics['total_fees']:,.2f}")
    summary.add_row("Total Trades", str(metrics["total_trades"]))

    console.print(summary)

    risk_table = Table(title="Risk & Performance Metrics", show_header=True)
    risk_table.add_column("Metric", style="bold")
    risk_table.add_column("Value", justify="right")

    risk_table.add_row("Sharpe Ratio", f"{metrics['sharpe_ratio']:.3f}")
    risk_table.add_row("Sortino Ratio", f"{metrics['sortino_ratio']:.3f}")
    risk_table.add_row("Calmar Ratio", f"{metrics['calmar_ratio']:.3f}")
    risk_table.add_row("Max Drawdown", f"{metrics['max_drawdown_pct']:.2f}%")

    if metrics["max_drawdown_start"]:
        risk_table.add_row(
            "Drawdown Period",
            f"{metrics['max_drawdown_start']} -> {metrics['max_drawdown_end']}",
        )

    risk_table.add_row("Win Rate", f"{metrics['win_rate_pct']:.1f}%")
    pf = metrics["profit_factor"]
    pf_text = f"{pf:.2f}" if pf != float("inf") else "inf"
    risk_table.add_row("Profit Factor", pf_text)

    console.print(risk_table)

    if result.trades:
        trade_table = Table(title="Recent Trades", show_header=True)
        trade_table.add_column("Entry", style="dim")
        trade_table.add_column("Exit", style="dim")
        trade_table.add_column("Symbol")
        trade_table.add_column("Side")
        trade_table.add_column("Entry $")
        trade_table.add_column("Exit $")
        trade_table.add_column("P&L", justify="right")
        trade_table.add_column("P&L%", justify="right")

        for t in result.trades[-20:]:
            pnl_style = "green" if float(t.pnl) > 0 else "red"
            trade_table.add_row(
                t.entry_time.strftime("%m-%d %H:%M") if t.entry_time else "-",
                t.exit_time.strftime("%m-%d %H:%M") if t.exit_time else "-",
                t.symbol,
                t.side.value,
                f"{float(t.entry_price):.2f}",
                f"{float(t.exit_price or 0):.2f}",
                f"[{pnl_style}]${float(t.pnl):+.2f}[/{pnl_style}]",
                f"[{pnl_style}]{float(t.pnl_pct):+.2f}%[/{pnl_style}]",
            )

        console.print(trade_table)


def print_equity_chart(equity_curve: list[tuple[datetime, float]]) -> None:
    if len(equity_curve) < 2:
        console.print("[yellow]Not enough data for equity chart[/yellow]")
        return

    values = [v for _, v in equity_curve]
    min_val = min(values)
    max_val = max(values)
    val_range = max_val - min_val if max_val > min_val else 1

    height = 20
    width = 80
    step = max(1, len(values) // width)

    chart = [[" " for _ in range(width)] for _ in range(height)]

    for i in range(width):
        idx = min(i * step, len(values) - 1)
        normalized = int((values[idx] - min_val) / val_range * (height - 1))
        chart[height - 1 - normalized][i] = "█"

    output = "\n".join("".join(row) for row in chart)
    console.print(Panel(output, title="Equity Curve"))
