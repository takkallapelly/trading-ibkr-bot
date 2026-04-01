#!/usr/bin/env python3
"""
scripts/run_backtest_5min.py
─────────────────────────────
Run 5-minute backtest using local CSV files (5 years of data).

Reads from: C:/TradingBot/data/AAPL_5min.csv etc.

Usage:
    python scripts/run_backtest_5min.py
    python scripts/run_backtest_5min.py --data-dir "C:/TradingBot/data"
    python scripts/run_backtest_5min.py --start 2022-01-01 --end 2024-12-31
    python scripts/run_backtest_5min.py --tickers AAPL META MSFT
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import click
from src.logger import setup_logging, get_logger

setup_logging()
log = get_logger(__name__)


@click.command()
@click.option("--data-dir",  default="C:/TradingBot/data", help="Folder with CSV files")
@click.option("--start",     default="2021-01-01",         help="Start date YYYY-MM-DD")
@click.option("--end",       default=None,                 help="End date YYYY-MM-DD")
@click.option("--tickers",   multiple=True, default=[],    help="Tickers to test (default: all in folder)")
@click.option("--capital",   default=25000,                help="Starting capital USD")
@click.option("--output",    default=None,                 help="HTML report output path")
def main(data_dir, start, end, tickers, capital, output):

    from pathlib import Path
    from datetime import date

    from src.data.csv_loader import CSVLoader
    from src.data.intraday import _intraday_cfg
    from src.strategy.signals import SignalEngine
    from src.strategy.signal import Direction
    from src.risk.kelly import KellySizer
    from src.backtest.metrics import summary
    from src.backtest.report import generate_html_report
    from rich.console import Console
    from rich.table import Table
    from rich.progress import track

    console = Console()
    intraday_cfg = _intraday_cfg()

    # ── Load CSV files ────────────────────────────────────────────────────────
    loader = CSVLoader(data_dir=data_dir)
    available = loader.available_tickers()
    console.print(f"\n[cyan]Found tickers:[/cyan] {', '.join(available)}")

    ticker_list = list(tickers) if tickers else available

    # Filter to only tickers that have files
    ticker_list = [t for t in ticker_list if t in available]
    if not ticker_list:
        log.error(f"No matching tickers found in {data_dir}")
        return

    log.info(
        f"5-min CSV backtest | {start} → {end or 'today'} | "
        f"{len(ticker_list)} tickers | capital=${capital:,}"
    )

    # ── Load all data ─────────────────────────────────────────────────────────
    console.print(f"\n[cyan]Loading {len(ticker_list)} tickers from CSV...[/cyan]")
    all_data = {}
    for ticker in track(ticker_list, description="Loading CSV files..."):
        df = loader.load(ticker, start=start, end=end, compute_features=True)
        if not df.empty and len(df) > 50:
            all_data[ticker] = df

    if not all_data:
        log.error("No data loaded — check file paths and date range")
        return

    console.print(f"[green]Loaded {len(all_data)} tickers successfully[/green]")

    # ── Run bar-by-bar simulation ─────────────────────────────────────────────
    engine = SignalEngine(config=intraday_cfg)
    sizer  = KellySizer(
        capital=capital,
        kelly_fraction=0.5,
        max_position_usd=capital * 0.10,
        min_trades=20,
    )

    all_trades = []
    console.print(f"\n[cyan]Running simulation on 5-min bars...[/cyan]")

    for ticker in track(list(all_data.keys()), description="Simulating..."):
        df = all_data[ticker]
        trades = _simulate(ticker, df, engine, sizer, intraday_cfg)
        all_trades.extend(trades)
        log.info(f"{ticker}: {len(trades)} trades | {len(df):,} bars")

    if not all_trades:
        console.print(
            "\n[yellow]No trades generated.[/yellow]\n"
            "Possible reasons:\n"
            "  - Market was trending (RSI stays extreme without reversing)\n"
            "  - Signal score threshold too high — try lowering min_signal_score\n"
            "  - Date range too short\n"
        )
        return

    # ── Compute and print results ─────────────────────────────────────────────
    results = summary(all_trades, capital)
    _print_results(all_trades, results, list(all_data.keys()), console)

    # ── Save HTML report ──────────────────────────────────────────────────────
    first_df = list(all_data.values())[0]
    report_path = Path(output) if output else Path("reports") / f"backtest_5min_{date.today()}.html"
    report_path.parent.mkdir(exist_ok=True)

    generate_html_report(
        trades=all_trades, metrics=results,
        tickers=ticker_list,
        start=str(first_df.index[0].date()),
        end=str(first_df.index[-1].date()),
        capital=capital,
        output=report_path,
    )

    log.success(
        f"5-min backtest complete | "
        f"Sharpe={results['sharpe']:.2f} | "
        f"CAGR={results['cagr']:.1%} | "
        f"MaxDD={results['max_drawdown']:.1%} | "
        f"WinRate={results['win_rate']:.1%} | "
        f"Trades={results['n_trades']} | "
        f"Report → {report_path}"
    )


def _simulate(ticker, df, engine, sizer, cfg):
    """Bar-by-bar simulation on 5-min bars."""
    from src.strategy.signal import Direction

    trades    = []
    position  = None
    completed = []

    for i in range(len(df) - 1):
        current_bar = df.iloc[i]
        next_bar    = df.iloc[i + 1]

        # ── Check exit ────────────────────────────────────────────────────────
        if position is not None:
            high = float(next_bar.get("high", 0))
            low  = float(next_bar.get("low",  0))
            exit_price = exit_reason = None

            if position["side"] == "LONG":
                if low  <= position["stop"]:
                    exit_price, exit_reason = position["stop"],   "STOP_LOSS"
                elif high >= position["target"]:
                    exit_price, exit_reason = position["target"], "TAKE_PROFIT"
            else:
                if high >= position["stop"]:
                    exit_price, exit_reason = position["stop"],   "STOP_LOSS"
                elif low  <= position["target"]:
                    exit_price, exit_reason = position["target"], "TAKE_PROFIT"

            # Force close at end of trading day (avoid overnight holds)
            if exit_price is None:
                eastern = next_bar.name.tz_convert("America/New_York")
                if eastern.hour >= 15 and eastern.minute >= 45:
                    exit_price = float(next_bar.get("close", position["entry"]))
                    exit_reason = "EOD"

            if exit_price:
                pnl = (exit_price - position["entry"]) * position["qty"]
                if position["side"] == "SHORT":
                    pnl = -pnl
                pnl -= position["commission"]

                trade = {
                    "ticker":       ticker,
                    "side":         position["side"],
                    "entry_time":   str(position["entry_time"]),
                    "exit_time":    str(next_bar.name),
                    "entry_price":  position["entry"],
                    "exit_price":   exit_price,
                    "qty":          position["qty"],
                    "pnl":          round(pnl, 2),
                    "exit_reason":  exit_reason,
                    "signal_score": position["score"],
                    "bars_held":    i - position["entry_idx"],
                }
                trades.append(trade)
                completed.append(trade)
                position = None
                continue

        # ── Look for new signal ───────────────────────────────────────────────
        if position is None:
            # Skip if near end of day (don't open positions in last 15 min)
            try:
                eastern = current_bar.name.tz_convert("America/New_York")
                if eastern.hour >= 15 and eastern.minute >= 45:
                    continue
                if eastern.hour == 9 and eastern.minute < 45:
                    continue
            except Exception:
                pass

            signal = engine.evaluate(ticker, current_bar)
            long_only = cfg.get("signals", {}).get("long_only", True)

            if (signal.direction != Direction.FLAT
                    and signal.score >= engine.min_score
                    and signal.stop_price > 0
                    and not (long_only and signal.direction == Direction.SHORT)):

                # Fill at next bar open + slippage
                fill  = float(next_bar.get("open", signal.entry_price))
                slip  = fill * 0.0005  # 5bps slippage
                fill  = fill + slip if signal.direction == Direction.LONG else fill - slip

                # Kelly sizing
                wins   = [t["pnl"] for t in completed if t["pnl"] > 0]
                losses = [t["pnl"] for t in completed if t["pnl"] < 0]
                wr  = len(wins)/len(completed) if completed else 0.44
                aw  = sum(wins)/len(wins)       if wins   else 50.0
                al  = abs(sum(losses)/len(losses)) if losses else 33.0
                pos_usd    = sizer.position_size_usd(wr, aw, al, len(completed))
                qty        = max(1, int(pos_usd / fill))
                commission = qty * 0.005 * 2  # round-trip

                position = {
                    "side":       signal.direction.value,
                    "entry":      round(fill, 4),
                    "stop":       signal.stop_price,
                    "target":     signal.target_price,
                    "qty":        qty,
                    "score":      signal.score,
                    "entry_time": next_bar.name,
                    "entry_idx":  i + 1,
                    "commission": commission,
                }

    return trades


def _print_results(all_trades, results, tickers, console):
    from rich.table import Table

    # Per-ticker breakdown
    ticker_table = Table(title="5-min backtest — per ticker", show_lines=True)
    ticker_table.add_column("Ticker",    style="cyan")
    ticker_table.add_column("Trades",    style="white",  justify="right")
    ticker_table.add_column("Win %",     style="green",  justify="right")
    ticker_table.add_column("P&L $",     style="yellow", justify="right")
    ticker_table.add_column("Best",      style="green",  justify="right")
    ticker_table.add_column("Worst",     style="red",    justify="right")
    ticker_table.add_column("Exp/trade", style="white",  justify="right")

    by_ticker = {}
    for t in all_trades:
        by_ticker.setdefault(t["ticker"], []).append(t)

    for ticker in sorted(by_ticker):
        ts   = by_ticker[ticker]
        pnls = [t["pnl"] for t in ts if t.get("pnl") is not None]
        if not pnls: continue
        wr = sum(1 for p in pnls if p > 0) / len(pnls)
        ticker_table.add_row(
            ticker, str(len(pnls)), f"{wr:.1%}",
            f"${sum(pnls):,.0f}", f"${max(pnls):,.0f}",
            f"${min(pnls):,.0f}", f"${sum(pnls)/len(pnls):.2f}",
        )
    console.print(ticker_table)

    # Overall metrics
    overall = Table(title="Overall 5-min performance", show_lines=True)
    overall.add_column("Metric", style="cyan")
    overall.add_column("Value",  style="yellow", justify="right")
    for k, v in [
        ("Total trades",      str(results["n_trades"])),
        ("Win rate",          f"{results['win_rate']:.1%}"),
        ("Profit factor",     f"{results['profit_factor']:.2f}"),
        ("Expectancy/trade",  f"${results['expectancy']:.2f}"),
        ("Total P&L",         f"${results['total_pnl']:,.0f}"),
        ("Total return",      f"{results['total_return']:.1%}"),
        ("Sharpe ratio",      f"{results['sharpe']:.2f}"),
        ("Sortino ratio",     f"{results['sortino']:.2f}"),
        ("Max drawdown",      f"{results['max_drawdown']:.1%}"),
        ("Initial capital",   f"${results['initial_capital']:,.0f}"),
        ("Final equity",      f"${results['final_equity']:,.0f}"),
    ]:
        overall.add_row(k, v)
    console.print(overall)


if __name__ == "__main__":
    main()
