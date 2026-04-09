#!/usr/bin/env python3
"""
scripts/run_live.py
───────────────────
Launch the live IBKR trading bot.
Usage:
    python scripts/run_live.py               # uses TRADING_MODE from .env
    python scripts/run_live.py --paper       # force paper mode
    python scripts/run_live.py --live        # force live (requires confirmation)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sys
import time
import atexit
import signal
import click
from datetime import datetime
from zoneinfo import ZoneInfo

from src.logger import setup_logging, get_logger
from src.config import settings

setup_logging()
log = get_logger(__name__)

# Global trader reference for shutdown handler
_trader = None


def _shutdown(signum=None, frame=None):
    """
    Called on SIGTERM, SIGINT, or atexit.
    live_trader.stop() already sends the bot_stopped Telegram alert
    so we just call stop() here — no duplicate alerts.
    """
    global _trader
    if _trader:
        try:
            _trader.stop()   # handles Telegram bot_stopped internally
        except Exception:
            pass
        _trader = None


# Register for ALL exit paths including terminal close (X button)
atexit.register(_shutdown)
signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT,  _shutdown)   # Ctrl+C (Strg+C on Windows)
try:
    signal.signal(signal.SIGBREAK, _shutdown)   # Windows Ctrl+Break
except AttributeError:
    pass


def _wait_for_market():
    """Block until 09:45 ET (15:45 CET). Print countdown every minute."""
    ET  = ZoneInfo("America/New_York")
    CET = ZoneInfo("Europe/Berlin")

    TRADABLE_H, TRADABLE_M = 9,  45
    CLOSE_H,    CLOSE_M    = 15, 45

    printed_header = False

    while True:
        now_et  = datetime.now(ET)
        now_cet = datetime.now(CET)
        h, m    = now_et.hour, now_et.minute
        weekday = now_et.weekday()

        if weekday >= 5:
            log.info(f"Weekend | CET={now_cet.strftime('%H:%M')} | waiting for Monday 15:45 CET")
            time.sleep(300)
            continue

        past_close = (h > CLOSE_H) or (h == CLOSE_H and m >= CLOSE_M)
        if past_close:
            log.info(f"Past trading window | CET={now_cet.strftime('%H:%M')} | resumes tomorrow 15:45 CET")
            time.sleep(300)
            continue

        before_tradable = (h < TRADABLE_H) or (h == TRADABLE_H and m < TRADABLE_M)
        if before_tradable:
            mins_left = (TRADABLE_H * 60 + TRADABLE_M) - (h * 60 + m)
            if not printed_header:
                log.info(
                    "Bot READY — waiting for market.\n"
                    "  Trading starts: 09:45 ET = 15:45 CET\n"
                    "  You do NOT need to restart — auto-starts at 15:45 CET."
                )
                printed_header = True
            log.info(f"Pre-market | CET={now_cet.strftime('%H:%M')} | ET={now_et.strftime('%H:%M %Z')} | starts in {mins_left} min")
            time.sleep(60)
            continue

        log.info(f"Market tradable | CET={now_cet.strftime('%H:%M')} | ET={now_et.strftime('%H:%M %Z')} | Starting now")
        return


def _patch_rsi_logging():
    """
    Patch signal scan to log RSI/score at INFO level every scan.
    Lets you see exactly what the bot sees without changing log level to DEBUG.
    """
    from src.execution import live_trader as lt_module

    original = lt_module.LiveTrader._scan_intraday_signals

    def patched(self):
        try:
            latest = self._intraday.get_latest_all()
            if latest:
                parts = []
                for t, bar in sorted(latest.items()):
                    rsi   = float(bar.get("rsi_2",        50))
                    score = float(bar.get("signal_score",  0))
                    close = float(bar.get("close",          0))
                    bb    = float(bar.get("bb_pct",       0.5))
                    if rsi < 15:
                        status = "OVERSOLD"
                    elif rsi > 85:
                        status = "OVERBOUGHT"
                    else:
                        status = "neutral"
                    parts.append(
                        f"{t}: close=${close:.0f}  RSI={rsi:.1f}({status})"
                        f"  score={score:.2f}  bb%={bb:.2f}"
                    )
                log.info("Bar snapshot:\n  " + "\n  ".join(parts))
        except Exception:
            pass
        return original(self)

    lt_module.LiveTrader._scan_intraday_signals = patched
    log.info("RSI logging patch active — values printed every 60-second scan")


@click.command()
@click.option("--paper", "mode", flag_value="paper", help="Force paper trading mode")
@click.option("--live",  "mode", flag_value="live",  help="Force live trading mode")
def main(mode: str | None) -> None:
    global _trader

    effective_mode = mode or settings.TRADING_MODE

    if effective_mode == "live":
        if settings.TRADING_MODE != "live":
            log.error("Refusing to go live: set TRADING_MODE=live in .env first.")
            sys.exit(1)
        click.confirm(
            f"\n⚠  LIVE trading with ${settings.TOTAL_CAPITAL:,.0f}. Sure?",
            abort=True,
        )

    log.info(
        f"Starting bot | mode={effective_mode} | "
        f"host={settings.IBKR_HOST}:{settings.IBKR_PORT} | "
        f"capital=${settings.TOTAL_CAPITAL:,.0f}"
    )
    settings.validate()

    _wait_for_market()
    _patch_rsi_logging()

    from src.execution.live_trader import LiveTrader
    _trader = LiveTrader(mode=effective_mode)

    # NOTE: bot_started Telegram is sent inside LiveTrader.start()
    # Do NOT call it here — would cause duplicate messages.
    try:
        _trader.start()
    except KeyboardInterrupt:
        log.warning("Keyboard interrupt — shutting down")
    except Exception as e:
        log.critical(f"Unexpected error: {e}")
        raise
    # _shutdown() fires via atexit — calls _trader.stop() which sends bot_stopped


if __name__ == "__main__":
    main()
