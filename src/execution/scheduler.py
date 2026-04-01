"""
src/execution/scheduler.py
───────────────────────────
Market schedule and daily task scheduler.

Handles:
  - Is the market open right now?
  - Is it safe to trade (microstructure guard)?
  - When to refresh data, reset circuit breakers, retrain ML
  - Handles US market holidays (via pandas_market_calendars)
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, date
from zoneinfo import ZoneInfo
from loguru import logger


EASTERN = ZoneInfo("America/New_York")

# US market hours (Eastern)
MARKET_OPEN  = time(9, 30)
MARKET_CLOSE = time(16, 0)

# Microstructure guard windows (Harris — Trading and Exchanges)
AVOID_OPEN_MINUTES  = 15   # avoid first 15 min after open
AVOID_CLOSE_MINUTES = 15   # avoid last 15 min before close


def now_eastern() -> datetime:
    """Current time in US Eastern timezone."""
    return datetime.now(tz=EASTERN)


def is_market_open() -> bool:
    """True if US equity market is currently open."""
    now = now_eastern()

    # Weekend check
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False

    # Hours check
    t = now.time()
    if t < MARKET_OPEN or t >= MARKET_CLOSE:
        return False

    # Holiday check (approximate — use pandas_market_calendars for precision)
    try:
        import pandas_market_calendars as mcal
        nyse = mcal.get_calendar("NYSE")
        schedule = nyse.schedule(
            start_date=now.strftime("%Y-%m-%d"),
            end_date=now.strftime("%Y-%m-%d"),
        )
        if schedule.empty:
            return False  # holiday
    except Exception:
        pass  # if calendar not available, rely on weekday/hours check

    return True


def is_tradable_time(
    avoid_open: int = AVOID_OPEN_MINUTES,
    avoid_close: int = AVOID_CLOSE_MINUTES,
) -> bool:
    """
    True if it's safe to place orders right now.

    Avoids the opening and closing turbulence windows.
    Harris (Trading and Exchanges): spreads are widest,
    institutional flow is most aggressive, at open and close.
    """
    if not is_market_open():
        return False

    now = now_eastern()
    t   = now.time()

    open_cutoff  = time(9, 30 + avoid_open)
    close_hour   = 16
    close_min    = 0 - avoid_close
    if close_min < 0:
        close_hour -= 1
        close_min  += 60
    close_cutoff = time(close_hour, close_min)

    if t < open_cutoff:
        logger.debug(f"Within first {avoid_open}min of open — no trading")
        return False
    if t > close_cutoff:
        logger.debug(f"Within last {avoid_close}min of close — no trading")
        return False

    return True


def minutes_to_open() -> float:
    """Minutes until market opens. Negative if already open."""
    now = now_eastern()
    today_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now < today_open:
        return (today_open - now).total_seconds() / 60
    return -1.0  # already open


def minutes_to_close() -> float:
    """Minutes until market closes. Negative if already closed."""
    now = now_eastern()
    today_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    if now < today_close:
        return (today_close - now).total_seconds() / 60
    return -1.0


def is_sunday() -> bool:
    """True if today is Sunday (ML retrain day)."""
    return date.today().weekday() == 6


def market_status() -> dict:
    """Return a dict describing current market status."""
    open_flag   = is_market_open()
    tradable    = is_tradable_time() if open_flag else False
    now         = now_eastern()

    return {
        "is_open":       open_flag,
        "is_tradable":   tradable,
        "time_eastern":  now.strftime("%H:%M:%S ET"),
        "date":          now.strftime("%Y-%m-%d"),
        "weekday":       now.strftime("%A"),
        "mins_to_open":  max(0, minutes_to_open()),
        "mins_to_close": max(0, minutes_to_close()),
    }
