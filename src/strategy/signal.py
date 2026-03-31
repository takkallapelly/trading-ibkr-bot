"""
src/strategy/signal.py
──────────────────────
The Signal dataclass — the single object the engine produces
and the rest of the system (risk manager, executor) consumes.

Think of it like a doctor's prescription:
  - What to do   (direction: LONG / SHORT / FLAT)
  - How confident (score: 0.0 → 1.0)
  - Entry price   (where to get in)
  - Stop price    (where to get out if wrong)
  - Target price  (where to take profit)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Direction(str, Enum):
    LONG  = "LONG"   # buy — expecting price to rise
    SHORT = "SHORT"  # sell — expecting price to fall
    FLAT  = "FLAT"   # no trade


@dataclass
class Signal:
    """
    A fully-specified trade signal produced by the SignalEngine.

    Attributes:
        ticker        : e.g. "TSLA"
        direction     : LONG, SHORT, or FLAT
        score         : composite signal strength 0.0 – 1.0
        timestamp     : bar time this signal was generated from
        close         : close price at signal time
        entry_price   : suggested limit/market entry price
        stop_price    : stop loss price (based on ATR)
        target_price  : take profit price (based on ATR)
        atr           : ATR value used to calculate stops
        rsi           : RSI(2) value at signal time
        bb_pct        : Bollinger Band % at signal time
        ema_diff      : EMA(9) - EMA(20) at signal time
        vol_ratio     : volume ratio vs 20-bar average
        meta_approved : True once ML meta-labeler has vetted this signal
        blocked_reason: why the signal was blocked (empty if not blocked)
    """

    ticker        : str
    direction     : Direction
    score         : float
    timestamp     : datetime
    close         : float
    entry_price   : float
    stop_price    : float
    target_price  : float
    atr           : float          = 0.0
    rsi           : float          = 50.0
    bb_pct        : float          = 0.5
    ema_diff      : float          = 0.0
    vol_ratio     : float          = 1.0
    meta_approved : bool           = True   # Phase 6 sets this to False until ML is trained
    blocked_reason: str            = ""

    @property
    def is_actionable(self) -> bool:
        """True if this signal should result in a trade."""
        return (
            self.direction != Direction.FLAT
            and self.score > 0.0
            and self.meta_approved
            and not self.blocked_reason
            and self.stop_price > 0
            and self.target_price > 0
            and self.entry_price > 0
        )

    @property
    def risk_reward(self) -> float:
        """Reward-to-risk ratio. Good trades have R:R > 1.0."""
        if self.direction == Direction.FLAT:
            return 0.0
        risk   = abs(self.entry_price - self.stop_price)
        reward = abs(self.target_price - self.entry_price)
        return round(reward / risk, 2) if risk > 0 else 0.0

    @property
    def risk_per_share(self) -> float:
        """Dollar risk per share = distance from entry to stop."""
        return abs(self.entry_price - self.stop_price)

    def to_dict(self) -> dict:
        return {
            "ticker":         self.ticker,
            "direction":      self.direction.value,
            "score":          round(self.score, 3),
            "timestamp":      str(self.timestamp),
            "close":          round(self.close, 2),
            "entry_price":    round(self.entry_price, 2),
            "stop_price":     round(self.stop_price, 2),
            "target_price":   round(self.target_price, 2),
            "risk_reward":    self.risk_reward,
            "rsi":            round(self.rsi, 1),
            "bb_pct":         round(self.bb_pct, 3),
            "ema_diff":       round(self.ema_diff, 4),
            "vol_ratio":      round(self.vol_ratio, 2),
            "meta_approved":  self.meta_approved,
            "blocked_reason": self.blocked_reason,
            "is_actionable":  self.is_actionable,
        }

    def __repr__(self) -> str:
        status = "✅ ACTIONABLE" if self.is_actionable else f"❌ BLOCKED ({self.blocked_reason})"
        return (
            f"Signal({self.ticker} {self.direction.value} "
            f"score={self.score:.2f} entry={self.entry_price:.2f} "
            f"stop={self.stop_price:.2f} target={self.target_price:.2f} "
            f"R:R={self.risk_reward} {status})"
        )


# ── Factory helpers ───────────────────────────────────────────────────────────

def flat_signal(ticker: str, reason: str = "", timestamp: datetime | None = None) -> Signal:
    """Create a FLAT (no-trade) signal with a reason."""
    return Signal(
        ticker=ticker,
        direction=Direction.FLAT,
        score=0.0,
        timestamp=timestamp or datetime.utcnow(),
        close=0.0,
        entry_price=0.0,
        stop_price=0.0,
        target_price=0.0,
        blocked_reason=reason,
    )
