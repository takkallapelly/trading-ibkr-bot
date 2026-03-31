#!/usr/bin/env python3
"""
scripts/run_backtest.py
───────────────────────
Run the walk-forward backtester across the full ticker universe.
Produces an HTML report in reports/.

Usage:
    python scripts/run_backtest.py
    python scripts/run_backtest.py --start 2022-01-01 --end 2024-01-01
    python scripts/run_backtest.py --tickers TSLA NVDA --output reports/custom.html
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import click
from pathlib import Path
from src.logger import setup_logging, get_logger
from src.config import settings, cfg, TICKERS

setup_logging()
log = get_logger(__name__)


@click.command()
@click.option("--start", default=cfg["backtest"]["start_date"], help="Start date YYYY-MM-DD")
@click.option("--end",   default=cfg["backtest"]["end_date"],   help="End date YYYY-MM-DD")
@click.option("--tickers", multiple=True, default=TICKERS,      help="Tickers to backtest")
@click.option("--output", default=None,                         help="HTML report output path")
def main(start: str, end: str, tickers: tuple, output: str | None) -> None:
    log.info(f"Starting backtest | {start} → {end} | tickers={list(tickers)}")

    # Phase 4 implementation imported here to keep startup fast
    from src.backtest.engine import Backtester

    output_path = Path(output) if output else settings.REPORTS_DIR / f"backtest_{start}_{end}.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    bt = Backtester(
        tickers=list(tickers),
        start=start,
        end=end,
        capital=settings.TOTAL_CAPITAL,
    )
    results = bt.run()
    bt.to_html(output_path)

    log.success(
        f"Backtest complete | Sharpe={results['sharpe']:.2f} | "
        f"CAGR={results['cagr']:.1%} | MaxDD={results['max_drawdown']:.1%} | "
        f"Report → {output_path}"
    )


if __name__ == "__main__":
    main()
