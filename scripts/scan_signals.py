#!/usr/bin/env python3
"""
scripts/scan_signals.py
────────────────────────
Scan all tickers right now and print a signal table.
Run this any time to see what the bot would trade.

Usage:
    python scripts/scan_signals.py
    python scripts/scan_signals.py --tickers TSLA NVDA
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import click
from src.logger import setup_logging, get_logger
from src.config import TICKERS

setup_logging()
log = get_logger(__name__)


@click.command()
@click.option("--tickers", multiple=True, default=[], help="Tickers to scan (default: all)")
def main(tickers):
    from src.data.loader import DataLoader
    from src.strategy.engine import StrategyEngine

    scan_tickers = list(tickers) if tickers else TICKERS

    loader = DataLoader(tickers=scan_tickers)
    engine = StrategyEngine(tickers=scan_tickers)

    log.info(f"Scanning {len(scan_tickers)} tickers for signals...")
    signals = engine.scan_all_and_print(loader=loader)

    if signals:
        log.success(f"{len(signals)} actionable signal(s) found!")
    else:
        log.info("No actionable signals right now.")


if __name__ == "__main__":
    main()
