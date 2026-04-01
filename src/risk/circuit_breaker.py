"""
src/risk/circuit_breaker.py
────────────────────────────
Circuit breakers — conditions that halt all trading immediately.

Inspired by Nassim Taleb (The Black Swan):
  "The strategy must survive the worst day, not just the average day."

Breakers implemented:
  1. Daily drawdown limit   — halt if down X% on the day
  2. Consecutive losses     — halt after N losses in a row
  3. Portfolio heat         — block new trades if too much capital at risk
  4. Correlation guard      — block new trade if correlated position open
  5. Max open positions     — block if already at position limit

Each breaker returns (halted: bool, reason: str).
The RiskManager checks all breakers before approving any new trade.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger


# ── Breaker 1: Daily drawdown limit ───────────────────────────────────────────

class DailyDrawdownBreaker:
    """
    Halt trading for the rest of the day if daily P&L drops below limit.

    Why: A bad day is often a sign of unusual market conditions.
    Trading through it compounds losses. Better to sit out.
    (Taleb: know when NOT to play.)
    """

    def __init__(self, limit_pct: float = 0.03, capital: float = 25_000.0):
        self.limit_pct = limit_pct     # e.g. 0.03 = halt if down 3%
        self.capital   = capital
        self._halted   = False
        self._reset_date: str | None = None

    def check(self, daily_pnl: float, today: str | None = None) -> tuple[bool, str]:
        """
        Check if daily drawdown limit is breached.

        Args:
            daily_pnl : today's P&L in USD (negative = loss)
            today     : date string "YYYY-MM-DD" for auto-reset

        Returns:
            (True, reason) if halted, (False, "") if clear.
        """
        # Auto-reset at start of new day
        import datetime
        today = today or datetime.date.today().isoformat()
        if self._reset_date != today:
            self._halted    = False
            self._reset_date = today

        if self._halted:
            return True, f"daily drawdown halt active (loss exceeded {self.limit_pct:.1%})"

        loss_pct = abs(daily_pnl) / self.capital if daily_pnl < 0 else 0.0

        if loss_pct >= self.limit_pct:
            self._halted = True
            logger.warning(
                f"🛑 CIRCUIT BREAKER: Daily drawdown {loss_pct:.1%} "
                f"exceeded limit {self.limit_pct:.1%} — halting today"
            )
            return True, f"daily P&L {daily_pnl:,.0f} exceeded -{self.limit_pct:.1%} limit"

        return False, ""

    def reset(self) -> None:
        """Manually reset the breaker (e.g. start of new session)."""
        self._halted = False


# ── Breaker 2: Consecutive losses ─────────────────────────────────────────────

class ConsecutiveLossBreaker:
    """
    Halt after N consecutive losing trades.

    Why: A streak of losses often means the market regime has changed
    and our signals are no longer valid. Step back and reassess.
    """

    def __init__(self, max_losses: int = 5):
        self.max_losses  = max_losses
        self._streak     = 0
        self._halted     = False

    def record(self, pnl: float) -> None:
        """Call this every time a trade closes."""
        if pnl < 0:
            self._streak += 1
            if self._streak >= self.max_losses:
                self._halted = True
                logger.warning(
                    f"🛑 CIRCUIT BREAKER: {self._streak} consecutive losses "
                    f"— halting until manual reset"
                )
        else:
            self._streak = 0  # reset streak on any win

    def check(self) -> tuple[bool, str]:
        if self._halted:
            return True, f"{self._streak} consecutive losses — manual reset required"
        return False, ""

    def reset(self) -> None:
        """Call manually after reviewing the situation."""
        self._halted = False
        self._streak = 0
        logger.info("Consecutive loss breaker reset manually")

    @property
    def current_streak(self) -> int:
        return self._streak


# ── Breaker 3: Portfolio heat ──────────────────────────────────────────────────

class PortfolioHeatBreaker:
    """
    Block new trades if too much capital is already at risk.

    'Heat' = total USD currently in open positions.
    Max heat = 20% of capital by default.

    Why: Concentration risk. If 3 tech stocks all drop together
    (which they will in a crash), you want limited exposure.
    """

    def __init__(
        self,
        max_heat_pct: float = 0.20,
        capital: float = 25_000.0,
        max_positions: int = 3,
    ):
        self.max_heat_pct  = max_heat_pct
        self.capital       = capital
        self.max_positions = max_positions

    def check(
        self,
        open_positions: list[dict],
        new_position_usd: float,
    ) -> tuple[bool, str]:
        """
        Check if adding a new position would breach heat or count limits.

        Args:
            open_positions   : list of open position dicts (with 'size_usd')
            new_position_usd : size of proposed new position in USD

        Returns:
            (True, reason) if blocked, (False, "") if clear.
        """
        # Check position count
        if len(open_positions) >= self.max_positions:
            return (True,
                    f"max positions ({self.max_positions}) already open")

        # Check heat
        current_heat = sum(p.get("size_usd", 0) for p in open_positions)
        new_heat     = current_heat + new_position_usd
        heat_pct     = new_heat / self.capital

        if heat_pct > self.max_heat_pct:
            return (True,
                    f"portfolio heat {heat_pct:.1%} would exceed "
                    f"{self.max_heat_pct:.1%} limit")

        return False, ""


# ── Breaker 4: Correlation guard ───────────────────────────────────────────────

class CorrelationGuard:
    """
    Block new trades in stocks that are highly correlated
    with an already open position.

    Why: META and MSFT often move together. Opening both at once
    is not diversification — it doubles your sector risk.

    Uses a pre-computed correlation matrix from recent price data.
    Falls back to a hardcoded sector map if data is unavailable.
    """

    # Hardcoded high-correlation pairs (sector knowledge)
    CORRELATED_PAIRS: dict[str, list[str]] = {
        "META":  ["GOOGL", "AMZN"],   # digital advertising
        "GOOGL": ["META", "AMZN"],
        "MSFT":  ["AAPL"],            # enterprise tech
        "AAPL":  ["MSFT"],
        "NVDA":  ["AMD"],             # semiconductors
        "AMD":   ["NVDA"],
        "TSLA":  [],
        "AMZN":  ["META", "GOOGL"],
    }

    def __init__(self, threshold: float = 0.70):
        self.threshold    = threshold
        self._corr_matrix: pd.DataFrame | None = None

    def update_correlations(self, returns: pd.DataFrame) -> None:
        """Update correlation matrix from recent daily returns."""
        self._corr_matrix = returns.corr()

    def check(
        self,
        new_ticker: str,
        open_tickers: list[str],
    ) -> tuple[bool, str]:
        """
        Check if new_ticker is too correlated with any open position.

        Args:
            new_ticker   : ticker we want to open
            open_tickers : tickers with currently open positions

        Returns:
            (True, reason) if blocked, (False, "") if clear.
        """
        if not open_tickers:
            return False, ""

        # Use computed correlation matrix if available
        if self._corr_matrix is not None:
            for open_ticker in open_tickers:
                if (new_ticker in self._corr_matrix.columns
                        and open_ticker in self._corr_matrix.columns):
                    corr = self._corr_matrix.loc[new_ticker, open_ticker]
                    if corr >= self.threshold:
                        return (True,
                                f"{new_ticker} correlation {corr:.2f} with "
                                f"open {open_ticker} exceeds {self.threshold}")

        # Fall back to hardcoded pairs
        correlated = self.CORRELATED_PAIRS.get(new_ticker, [])
        for open_ticker in open_tickers:
            if open_ticker in correlated:
                return (True,
                        f"{new_ticker} is correlated with open position {open_ticker}")

        return False, ""
