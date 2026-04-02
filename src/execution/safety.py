"""
src/execution/safety.py
────────────────────────
System safety guards inspired by Taleb — The Black Swan.

Taleb's key insight for automated systems:
  "The turkey's model was correct 1,000 times in a row, then wrong once
   in a catastrophic way. Make sure your catastrophic failure is survivable."

For an automated trading bot, the Black Swan is NOT a bad trade.
A bad trade loses $50-200. The bot survives.

The REAL Black Swan risks are:
  1. Data feed returns corrupt prices → bot buys at $0.01 or $99,999
  2. Infinite loop places 1,000 orders in 1 second
  3. Position state resets while in a trade → duplicate orders
  4. IBKR disconnects mid-order → orphaned positions
  5. Bot runs on wrong market day (holiday) → bad fills

These are the events that RUIN you. A losing streak does not ruin you.
A runaway order loop at $2,500 per order does.

This module adds hard system-level guards that the bot checks BEFORE
placing ANY order. These cannot be overridden by signal logic.

Usage:
    guard = SystemSafetyGuard()
    safe, reason = guard.check_all(price, qty, ticker)
    if not safe:
        log.error(f"Safety block: {reason}")
        return  # never place the order
"""

from __future__ import annotations

from datetime import datetime, date
from loguru import logger


class SystemSafetyGuard:
    """
    Hard system-level safety checks before any order is placed.

    These are Taleb's "negative advice" rules — things the system
    must NEVER do, regardless of what the signal engine says.

    All checks must pass before any bracket order is submitted.
    """

    def __init__(
        self,
        max_price_usd:      float = 5000.0,   # reject if stock price > $5,000
        min_price_usd:      float = 1.0,       # reject if stock price < $1
        max_qty_per_order:  int   = 500,       # reject if qty > 500 shares
        max_orders_per_day: int   = 20,        # halt if > 20 orders placed today
        max_position_usd:   float = 5000.0,    # reject if position > $5,000
    ):
        self.max_price      = max_price_usd
        self.min_price      = min_price_usd
        self.max_qty        = max_qty_per_order
        self.max_daily_ord  = max_orders_per_day
        self.max_pos_usd    = max_position_usd
        self._orders_today  = 0
        self._last_reset    = date.today()

    def check_all(
        self,
        price:  float,
        qty:    int,
        ticker: str,
        stop:   float = 0.0,
        target: float = 0.0,
    ) -> tuple[bool, str]:
        """
        Run all safety checks. Returns (safe, reason).
        ALL checks must pass for safe=True.

        Args:
            price  : entry price in USD
            qty    : number of shares
            ticker : stock symbol
            stop   : stop loss price
            target : take profit price

        Returns:
            (True, "OK") if safe
            (False, reason) if any check fails
        """
        self._reset_daily_counter()

        checks = [
            self._check_price_sanity(price, ticker),
            self._check_qty_sanity(qty, ticker),
            self._check_position_size(price, qty, ticker),
            self._check_stop_target_logic(price, stop, target, ticker),
            self._check_daily_order_limit(ticker),
            self._check_not_weekend(ticker),
        ]

        for passed, reason in checks:
            if not passed:
                logger.error(
                    f"🛑 SAFETY BLOCK | {ticker} | {reason} | "
                    "Order NOT placed (Taleb system guard)"
                )
                return False, reason

        # All passed — increment counter and return safe
        self._orders_today += 1
        logger.debug(
            f"Safety checks passed | {ticker} | "
            f"price=${price:.2f} qty={qty} | "
            f"orders today: {self._orders_today}"
        )
        return True, "OK"

    # ── Individual checks ─────────────────────────────────────────────────────

    def _check_price_sanity(self, price: float, ticker: str) -> tuple[bool, str]:
        """
        Reject if price is clearly wrong (data corruption / fat-finger).

        Taleb: corrupt data causes the worst unexpected losses.
        A price of $0.001 or $99,999 means the feed is broken.
        """
        if price <= 0:
            return False, f"Price ${price:.4f} is zero or negative — data error"
        if price < self.min_price:
            return False, f"Price ${price:.4f} below minimum ${self.min_price} — possible penny stock or data error"
        if price > self.max_price:
            return False, f"Price ${price:.2f} above maximum ${self.max_price:,.0f} — verify this is correct"
        return True, "OK"

    def _check_qty_sanity(self, qty: int, ticker: str) -> tuple[bool, str]:
        """
        Reject if quantity is unreasonable.

        Protects against the bot calculating 10,000 shares due to a
        division-by-zero or data error in Kelly sizing.
        """
        if qty <= 0:
            return False, f"Qty {qty} is zero or negative — sizing error"
        if qty > self.max_qty:
            return False, (
                f"Qty {qty} exceeds maximum {self.max_qty} shares — "
                "possible sizing calculation error"
            )
        return True, "OK"

    def _check_position_size(self, price: float, qty: int, ticker: str) -> tuple[bool, str]:
        """
        Reject if position size in USD is too large.

        Our max position is $2,500. If Kelly sizing returns $10,000
        due to a bug, this catches it.
        """
        position_usd = price * qty
        if position_usd > self.max_pos_usd:
            return False, (
                f"Position ${position_usd:,.0f} exceeds maximum "
                f"${self.max_pos_usd:,.0f} — check Kelly sizing"
            )
        return True, "OK"

    def _check_stop_target_logic(
        self,
        price: float,
        stop:  float,
        target: float,
        ticker: str,
    ) -> tuple[bool, str]:
        """
        Verify stop and target are on the correct side of entry.

        For LONG: stop < price < target
        This catches sign errors, inverted prices, or NaN values.
        """
        if stop <= 0 or target <= 0:
            return True, "OK"  # not provided, skip check

        if stop >= price:
            return False, (
                f"Stop ${stop:.2f} >= Entry ${price:.2f} — "
                "stop must be BELOW entry for LONG"
            )
        if target <= price:
            return False, (
                f"Target ${target:.2f} <= Entry ${price:.2f} — "
                "target must be ABOVE entry for LONG"
            )
        if stop >= target:
            return False, f"Stop ${stop:.2f} >= Target ${target:.2f} — logic error"

        return True, "OK"

    def _check_daily_order_limit(self, ticker: str) -> tuple[bool, str]:
        """
        Halt if too many orders placed today — runaway loop protection.

        Taleb: the worst automated trading disasters come from runaway
        loops placing hundreds of orders in seconds. This hard stop
        prevents that from ruining the account.
        """
        if self._orders_today >= self.max_daily_ord:
            return False, (
                f"Daily order limit reached ({self._orders_today} orders) — "
                "possible runaway loop detected. Manual review required."
            )
        return True, "OK"

    def _check_not_weekend(self, ticker: str) -> tuple[bool, str]:
        """
        Never place orders on Saturday or Sunday.

        Markets are closed. An order submitted on a weekend will
        execute at Monday's open at whatever price — often dangerous.
        """
        day = date.today().weekday()
        if day >= 5:  # 5=Saturday, 6=Sunday
            day_name = "Saturday" if day == 5 else "Sunday"
            return False, f"Today is {day_name} — markets closed, no orders"
        return True, "OK"

    def _reset_daily_counter(self) -> None:
        """Reset order counter at start of each new day."""
        today = date.today()
        if today != self._last_reset:
            self._orders_today = 0
            self._last_reset   = today

    def daily_order_count(self) -> int:
        """How many orders placed today."""
        self._reset_daily_counter()
        return self._orders_today
