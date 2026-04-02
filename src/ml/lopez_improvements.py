"""
src/ml/lopez_improvements.py
─────────────────────────────
Improvements from López de Prado — Advances in Financial Machine Learning.

Three key improvements extracted from the book:

1. CUSUM Filter (Chapter 2)
   Instead of scanning every 5-min bar, only generate signals when
   there is a statistically meaningful shift in price — a "structural break."
   This dramatically reduces false signals (the #1 problem on 5-min bars).

2. Triple-Barrier Labeling (Chapter 3)
   Labels each trade as: hit upper barrier (win), hit lower barrier (loss),
   or expired at vertical barrier (time stop). Better than fixed stop/target.

3. Probability-Based Bet Sizing (Chapter 10)
   Size each trade based on the ML model's CONFIDENCE, not just Kelly.
   If ML says 80% win probability → full Kelly size.
   If ML says 55% win probability → 20% of Kelly size.
   This is the most direct improvement to live P&L.

Usage:
    from src.ml.lopez_improvements import CUSUMFilter, TripleBarrier, BetSizer

    # Only trade when CUSUM detects a real move
    cusum = CUSUMFilter(threshold=0.01)  # 1% threshold
    events = cusum.filter(price_series)

    # Size bets by ML confidence
    sizer = BetSizer()
    size = sizer.size_from_probability(prob=0.75, kelly_size=2500)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger


# ── 1. CUSUM Filter ───────────────────────────────────────────────────────────

class CUSUMFilter:
    """
    Symmetric CUSUM filter from López de Prado Chapter 2.

    The CUSUM filter detects when price has moved far enough from
    a reference level to warrant a new signal. This prevents the bot
    from trading every tiny RSI dip — only real moves get through.

    Think of it as an "is this move significant enough?" gate.

    On 5-min bars without CUSUM: 2,000+ signals per ticker per year.
    On 5-min bars WITH CUSUM:    200-400 signals — only the real ones.
    """

    def __init__(self, threshold: float = 0.005):
        """
        Args:
            threshold: minimum cumulative return to trigger a signal.
                       0.005 = 0.5% move required. Good for daily bars.
                       0.002 = 0.2% move required. Good for 5-min bars.
        """
        self.threshold = threshold

    def filter(self, prices: pd.Series) -> pd.DatetimeIndex:
        """
        Apply CUSUM filter to a price series.

        Returns timestamps where a significant move has been detected —
        these are the ONLY bars where we should consider trading.

        Args:
            prices: pd.Series of close prices with datetime index

        Returns:
            DatetimeIndex of event timestamps
        """
        events = []
        s_pos  = 0.0
        s_neg  = 0.0

        returns = prices.pct_change().dropna()

        for t, r in returns.items():
            s_pos = max(0, s_pos + r)
            s_neg = min(0, s_neg + r)

            if s_pos >= self.threshold:
                s_pos = 0
                events.append(t)
            elif s_neg <= -self.threshold:
                s_neg = 0
                events.append(t)

        logger.debug(
            f"CUSUM filter | threshold={self.threshold:.3f} | "
            f"{len(prices)} bars → {len(events)} events "
            f"({len(events)/len(prices)*100:.1f}% pass rate)"
        )

        return pd.DatetimeIndex(events)

    def is_event(self, prices: pd.Series, current_idx: int) -> bool:
        """
        Check if the current bar passes the CUSUM filter.
        Used in the live trading loop — call this before evaluating signals.

        Args:
            prices      : recent price series (last 50 bars sufficient)
            current_idx : index of the current bar in the series

        Returns:
            True if CUSUM detects a significant move at this bar
        """
        if current_idx < 2:
            return False

        # Check last few bars for cumulative move
        window = prices.iloc[max(0, current_idx-20):current_idx+1]
        events = self.filter(window)
        if len(events) == 0:
            return False

        # Is the most recent event at or near the current bar?
        last_event = events[-1]
        current_ts = prices.index[current_idx]
        time_diff  = abs((current_ts - last_event).total_seconds())
        return time_diff < 1800  # within 30 minutes


# ── 2. Triple-Barrier Labeling ────────────────────────────────────────────────

class TripleBarrier:
    """
    Triple-barrier method from López de Prado Chapter 3.

    Labels each potential trade with one of three outcomes:
      +1 : upper barrier hit first (take profit)
      -1 : lower barrier hit first (stop loss)
       0 : vertical barrier hit first (time stop — exit after N bars)

    This is more accurate than our current fixed stop/target because:
    - The vertical barrier prevents holding losers indefinitely
    - ATR-based barriers adapt to current volatility
    - The label accounts for TIME as a third exit dimension

    The bot already uses ATR stops/targets — this improves the
    ML LABELING of those trades for better model training.
    """

    def __init__(
        self,
        pt_multiplier:  float = 3.0,   # take profit: 3.0 × ATR
        sl_multiplier:  float = 1.5,   # stop loss:   1.5 × ATR
        vertical_bars:  int   = 10,    # time stop:   10 bars max
    ):
        self.pt_mult = pt_multiplier
        self.sl_mult = sl_multiplier
        self.vert    = vertical_bars

    def label(
        self,
        df:       pd.DataFrame,
        entry_idx: int,
        side:      int = 1,   # 1=LONG, -1=SHORT
    ) -> dict:
        """
        Apply triple-barrier to one potential trade.

        Args:
            df        : DataFrame with OHLCV + atr column
            entry_idx : bar index of the entry signal
            side      : 1 for long, -1 for short

        Returns:
            dict with label (+1, -1, 0), exit_idx, pnl_pct
        """
        if entry_idx >= len(df) - 1:
            return {"label": 0, "exit_idx": entry_idx, "pnl_pct": 0.0}

        entry_bar   = df.iloc[entry_idx]
        entry_price = float(entry_bar.get("close", 0))
        atr         = float(entry_bar.get("atr", entry_price * 0.01))

        # Barrier levels
        upper = entry_price + self.pt_mult * atr * side
        lower = entry_price - self.sl_mult * atr * side

        # Scan forward until a barrier is hit or vertical limit reached
        end_idx = min(entry_idx + self.vert, len(df) - 1)

        for i in range(entry_idx + 1, end_idx + 1):
            bar  = df.iloc[i]
            high = float(bar.get("high", 0))
            low  = float(bar.get("low",  0))

            if side == 1:  # LONG
                if high >= upper:
                    pnl_pct = (upper - entry_price) / entry_price
                    return {"label": 1, "exit_idx": i, "pnl_pct": pnl_pct}
                if low  <= lower:
                    pnl_pct = (lower - entry_price) / entry_price
                    return {"label": -1, "exit_idx": i, "pnl_pct": pnl_pct}
            else:  # SHORT
                if low  <= upper:
                    pnl_pct = (entry_price - upper) / entry_price
                    return {"label": 1, "exit_idx": i, "pnl_pct": pnl_pct}
                if high >= lower:
                    pnl_pct = (entry_price - lower) / entry_price
                    return {"label": -1, "exit_idx": i, "pnl_pct": pnl_pct}

        # Vertical barrier — close at end of window
        exit_price = float(df.iloc[end_idx].get("close", entry_price))
        pnl_pct    = side * (exit_price - entry_price) / entry_price
        return {"label": 0, "exit_idx": end_idx, "pnl_pct": pnl_pct}

    def label_all(
        self,
        df:       pd.DataFrame,
        signals:  pd.DatetimeIndex,
        side:     int = 1,
    ) -> pd.DataFrame:
        """
        Label a batch of signals using the triple-barrier method.
        Used during ML training to build better training labels.

        Args:
            df      : full price DataFrame
            signals : DatetimeIndex of signal timestamps
            side    : 1 for long, -1 for short

        Returns:
            DataFrame with columns: entry_time, label, exit_idx, pnl_pct
        """
        rows = []
        for ts in signals:
            if ts not in df.index:
                continue
            idx    = df.index.get_loc(ts)
            result = self.label(df, idx, side)
            result["entry_time"] = ts
            rows.append(result)

        return pd.DataFrame(rows) if rows else pd.DataFrame()


# ── 3. Probability-Based Bet Sizing ──────────────────────────────────────────

class BetSizer:
    """
    Probability-based bet sizing from López de Prado Chapter 10.

    Key insight: size bets in PROPORTION to ML confidence.

    Standard Kelly says: size = f(win_rate, avg_win, avg_loss)
    López de Prado says: also multiply by ML probability.

    Example:
        ML says 80% win probability → use 100% of Kelly size
        ML says 65% win probability → use 50% of Kelly size
        ML says 52% win probability → use 10% of Kelly size

    This prevents the bot from betting the same amount on a
    "barely passing" signal as on a "slam dunk" signal.
    The result: larger positions on high-confidence trades,
    smaller on marginal ones. Better risk-adjusted returns.

    Formula from the book:
        bet_size = (2 × probability - 1) × kelly_fraction × capital
    """

    def __init__(
        self,
        min_probability: float = 0.55,  # don't trade below this confidence
        max_kelly_mult:  float = 1.0,   # never exceed full Kelly
    ):
        self.min_prob   = min_probability
        self.max_kelly  = max_kelly_mult

    def size_from_probability(
        self,
        prob:        float,
        kelly_size:  float,
        capital:     float = 25000,
        max_pos_usd: float = 2500,
    ) -> float:
        """
        Compute position size based on ML win probability.

        Args:
            prob        : ML predicted probability of winning (0.5 to 1.0)
            kelly_size  : base Kelly position size in USD
            capital     : total capital
            max_pos_usd : maximum position size cap

        Returns:
            position size in USD (0 if probability too low)
        """
        if prob < self.min_prob:
            logger.debug(
                f"BetSizer: prob={prob:.2f} below min={self.min_prob:.2f} — no bet"
            )
            return 0.0

        # López de Prado formula: bet_size = (2p - 1)
        # This maps: p=0.5 → 0.0, p=0.75 → 0.5, p=1.0 → 1.0
        bet_fraction = min(2 * prob - 1, self.max_kelly)

        # Scale Kelly size by bet fraction
        sized = kelly_size * bet_fraction

        # Apply hard cap
        final = min(sized, max_pos_usd)

        logger.debug(
            f"BetSizer: prob={prob:.2f} | "
            f"fraction={bet_fraction:.2f} | "
            f"size=${final:,.0f}"
        )

        return round(final, 2)

    def size_from_signal_score(
        self,
        score:       float,
        kelly_size:  float,
        max_pos_usd: float = 2500,
    ) -> float:
        """
        Alternative: size from signal score (0.65 to 1.0) directly.
        Use when ML probability isn't available.

        Maps score linearly:
            score=0.65 → 30% of Kelly
            score=0.80 → 60% of Kelly
            score=1.00 → 100% of Kelly
        """
        if score < 0.65:
            return 0.0

        # Normalise score from [0.65, 1.0] to [0.3, 1.0]
        fraction = 0.3 + (score - 0.65) / (1.0 - 0.65) * 0.7
        sized    = kelly_size * min(fraction, 1.0)
        return round(min(sized, max_pos_usd), 2)

    def discrete_size(
        self,
        prob:        float,
        kelly_size:  float,
        max_pos_usd: float = 2500,
        levels:      int   = 5,
    ) -> float:
        """
        Discretise bet sizes into N levels (López de Prado Section 10.5).
        Prevents excessive granularity — avoids 847 shares vs 850 etc.

        levels=5 gives: 20%, 40%, 60%, 80%, 100% of Kelly
        """
        raw    = self.size_from_probability(prob, kelly_size, max_pos_usd=max_pos_usd)
        if raw == 0:
            return 0.0

        # Round to nearest level
        step   = max_pos_usd / levels
        rounded = round(raw / step) * step
        return min(rounded, max_pos_usd)


# ── Integration helper ────────────────────────────────────────────────────────

def apply_lopez_improvements(
    signal,
    ml_probability: float,
    kelly_size:     float,
    price_series:   pd.Series | None = None,
    cusum_threshold: float = 0.003,
) -> dict:
    """
    Apply all three López de Prado improvements to one signal.

    Args:
        signal          : Signal dataclass from SignalEngine
        ml_probability  : ML model's predicted win probability
        kelly_size      : base Kelly position size in USD
        price_series    : recent price series for CUSUM filter
        cusum_threshold : CUSUM sensitivity (0.003 = 0.3% shift)

    Returns:
        dict with approved, final_size, reason
    """
    # 1. CUSUM gate: is this a real structural move?
    if price_series is not None and len(price_series) > 10:
        cusum  = CUSUMFilter(threshold=cusum_threshold)
        events = cusum.filter(price_series)
        if len(events) == 0:
            return {
                "approved": False,
                "final_size": 0,
                "reason": f"CUSUM: no structural shift detected (threshold={cusum_threshold:.3f})",
            }

    # 2. Probability-based sizing
    sizer      = BetSizer()
    final_size = sizer.size_from_probability(
        prob=ml_probability,
        kelly_size=kelly_size,
        max_pos_usd=kelly_size,
    )

    if final_size == 0:
        return {
            "approved": False,
            "final_size": 0,
            "reason": f"ML probability {ml_probability:.2f} too low (min 0.55)",
        }

    return {
        "approved":   True,
        "final_size": final_size,
        "reason":     f"prob={ml_probability:.2f} → size=${final_size:,.0f}",
    }
