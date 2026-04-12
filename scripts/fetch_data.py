#!/usr/bin/env python3
"""
scripts/fetch_data.py
─────────────────────
One-time script to download 5 years of daily data for all tickers
and build the feature database. Run this before backtesting.

Usage:
    python scripts/fetch_data.py
    python scripts/fetch_data.py --start 2020-01-01
    python scripts/fetch_data.py --tickers TSLA NVDA --force
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import click

from src.logger import setup_logging, get_logger
from src.config import TICKERS

setup_logging()
log = get_logger(__name__)


@click.command()
@click.option("--start",   default="2019-01-01", help="Start date YYYY-MM-DD")
@click.option("--end",     default=None,          help="End date YYYY-MM-DD (default: today)")
@click.option("--tickers", multiple=True,          help="Specific tickers (default: all)")
@click.option("--force",   is_flag=True,           help="Re-download even if cached")
@click.option("--interval",default="1d",           help="Bar size: 1d, 1h, 5m")
def main(start, end, tickers, force, interval):
    from src.data.loader import DataLoader

    ticker_list = list(tickers) if tickers else TICKERS
    log.info(f"Fetching {len(ticker_list)} tickers | {start} → {end or 'today'} | {interval}")

    loader = DataLoader(tickers=ticker_list, interval=interval)
    results = loader.fetch_all(start=start, end=end, force=force)

    log.success(
        f"Done! {len(results)}/{len(ticker_list)} tickers loaded. "
        "Run 'python scripts/run_backtest.py' next."
    )


if __name__ == "__main__":
    main()
