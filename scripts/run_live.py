#!/usr/bin/env python3
"""
scripts/run_live.py
───────────────────
Launch the live IBKR trading bot.

Usage:
    python scripts/run_live.py               # uses TRADING_MODE from .env
    python scripts/run_live.py --paper       # force paper mode
    python scripts/run_live.py --live        # force live (requires confirmation)

Safety:  The bot will REFUSE to place live orders unless TRADING_MODE=live
         is explicitly set in .env AND --live flag is passed.
         Two-factor protection against accidental live trading.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sys
import click
from src.logger import setup_logging, get_logger
from src.config import settings

setup_logging()
log = get_logger(__name__)


@click.command()
@click.option("--paper", "mode", flag_value="paper", help="Force paper trading mode")
@click.option("--live",  "mode", flag_value="live",  help="Force live trading mode")
def main(mode: str | None) -> None:
    effective_mode = mode or settings.TRADING_MODE

    # ── Live mode safety gate ─────────────────────────────────────────────────
    if effective_mode == "live":
        if settings.TRADING_MODE != "live":
            log.error(
                "Refusing to go live: TRADING_MODE in .env is not 'live'. "
                "Set TRADING_MODE=live in your .env file first."
            )
            sys.exit(1)
        click.confirm(
            f"\n⚠  You are about to start LIVE trading with ${settings.TOTAL_CAPITAL:,.0f}. "
            "Are you absolutely sure?",
            abort=True,
        )

    log.info(
        f"Starting bot | mode={effective_mode} | "
        f"host={settings.IBKR_HOST}:{settings.IBKR_PORT} | "
        f"capital=${settings.TOTAL_CAPITAL:,.0f}"
    )

    settings.validate()

    # ── Import heavy modules after config validation ──────────────────────────
    from src.execution.live_trader import LiveTrader

    trader = LiveTrader(mode=effective_mode)
    try:
        trader.start()
    except KeyboardInterrupt:
        log.warning("Keyboard interrupt received — shutting down gracefully")
        trader.stop()
    except Exception as e:
        log.critical(f"Unexpected error: {e}")
        trader.stop()
        raise


if __name__ == "__main__":
    main()
