"""
src/strategy/rules.py
─────────────────────
Guard rules — each rule can BLOCK a signal before it reaches the executor.
Think of these as bouncers at a club door.

Rules implemented:
  1. Microstructure guard  — no trades in first/last 15 min (Harris)
  2. Min score threshold   — signal must be strong enough
  3. Min ATR filter        — skip stocks with almost no volatility today
  4. Volume filter         — skip low-volume bars (easy to get bad fills)
  5. Direction consistency — RSI and BB must agree on direction
  6. Risk-reward filter    — skip trades with R:R < 1.0

Each rule is a plain function: (bar, cfg) → (blocked: bool, reason: str)
Easy to add new rules or disable existing ones in config.yaml.
"""

from __future__ import annotations

import pandas as pd
from datetime import time, timezone


# ── Rule 1: Market microstructure guard (Harris — Trading and Exchanges) ───────

def check_market_hours(
    bar: pd.Series,
    avoid_open_min: int = 15,
    avoid_close_min: int = 15,
) -> tuple[bool, str]:
    """
    Block trades in the first and last N minutes of the session.

    Why: Spreads are widest, institutional order flow is most aggressive,
    and price moves are least predictable at the open and close.
    Harris calls this the 'opening/closing rotation' risk.
    """
    try:
        ts = bar.name
        # Convert to US Eastern time
        if hasattr(ts, 'tz_convert'):
            eastern = ts.tz_convert("America/New_York")
        else:
            return False, ""  # can't check — allow it

        t = eastern.time()
        open_cutoff  = time(9, 30 + avoid_open_min)
        close_cutoff = time(16, 0)

        # Calculate close cutoff properly
        close_minutes = 16 * 60 - avoid_close_min
        close_cutoff_h = close_minutes // 60
        close_cutoff_m = close_minutes % 60
        close_cutoff = time(close_cutoff_h, close_cutoff_m)

        if t < open_cutoff:
            return True, f"within first {avoid_open_min}min of open"
        if t > close_cutoff:
            return True, f"within last {avoid_close_min}min of close"

    except Exception:
        pass  # if we can't parse the time, don't block

    return False, ""


# ── Rule 2: Minimum signal score ───────────────────────────────────────────────

def check_min_score(
    score: float,
    min_score: float = 0.60,
) -> tuple[bool, str]:
    """
    Block signals that aren't strong enough.
    With weights 0.5/0.3/0.2, score of 0.60 means at minimum
    RSI(2) + Bollinger Band must both agree.
    """
    if abs(score) < min_score:
        return True, f"score {score:.2f} below threshold {min_score}"
    return False, ""


# ── Rule 3: Minimum ATR filter ─────────────────────────────────────────────────

def check_min_atr(
    bar: pd.Series,
    min_atr_pct: float = 0.005,
) -> tuple[bool, str]:
    """
    Skip stocks that aren't moving enough today.
    ATR < 0.5% of price = too tight to make money after commissions.
    """
    atr_pct = bar.get("atr_pct", 1.0)
    if atr_pct < min_atr_pct:
        return True, f"ATR% {atr_pct:.3f} too low (min {min_atr_pct})"
    return False, ""


# ── Rule 4: Volume filter ──────────────────────────────────────────────────────

def check_volume(
    bar: pd.Series,
    min_vol_ratio: float = 0.5,   # at least 50% of average volume
) -> tuple[bool, str]:
    """
    Skip bars with very low volume.
    Low volume = wide spreads, poor fills, unreliable signals.
    """
    vol_ratio = bar.get("vol_ratio", 1.0)
    if pd.isna(vol_ratio):
        return False, ""
    if vol_ratio < min_vol_ratio:
        return True, f"low volume (ratio={vol_ratio:.2f}, min={min_vol_ratio})"
    return False, ""


# ── Rule 5: Direction consistency ──────────────────────────────────────────────

def check_direction_consistency(
    bar: pd.Series,
    direction: str,   # "LONG" or "SHORT"
) -> tuple[bool, str]:
    """
    RSI signal and BB signal must agree on direction.
    If RSI says LONG but BB says SHORT — something is confused, skip it.

    This filters out the 'whipsaw zone' between the two indicators.
    """
    rsi_signal = bar.get("rsi_signal", 0)
    bb_signal  = bar.get("bb_signal", 0)

    if direction == "LONG":
        # RSI and BB should both be non-negative (at least neutral)
        if rsi_signal < 0 or bb_signal < 0:
            return True, f"direction conflict: rsi={rsi_signal} bb={bb_signal}"
    elif direction == "SHORT":
        if rsi_signal > 0 or bb_signal > 0:
            return True, f"direction conflict: rsi={rsi_signal} bb={bb_signal}"

    return False, ""


# ── Rule 6: Risk-reward filter ─────────────────────────────────────────────────

def check_risk_reward(
    entry: float,
    stop: float,
    target: float,
    min_rr: float = 1.0,
) -> tuple[bool, str]:
    """
    Only take trades where reward >= risk.
    R:R < 1.0 means you need a win rate > 50% just to break even.
    With our ~60% win rate target, R:R >= 1.0 keeps expectancy positive.
    """
    risk   = abs(entry - stop)
    reward = abs(target - entry)
    if risk == 0:
        return True, "zero risk distance"
    rr = reward / risk
    if rr < min_rr:
        return True, f"R:R {rr:.2f} below minimum {min_rr}"
    return False, ""


# ── Apply all rules ────────────────────────────────────────────────────────────

def apply_all_rules(
    bar: pd.Series,
    score: float,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    cfg: dict | None = None,
) -> tuple[bool, str]:
    """
    Run every rule in sequence. Return (blocked, reason) for the first
    rule that fires, or (False, "") if all rules pass.

    Args:
        bar       : latest OHLCV + feature bar
        score     : composite signal score
        direction : "LONG" or "SHORT"
        entry     : proposed entry price
        stop      : proposed stop price
        target    : proposed target price
        cfg       : config.yaml dict

    Returns:
        (True, reason)  — signal is blocked
        (False, "")     — all rules passed, signal is valid
    """
    c = cfg or {}
    sig  = c.get("signals", {})
    risk = c.get("risk", {})

    rules = [
        lambda: check_market_hours(
            bar,
            avoid_open_min  = sig.get("avoid_first_minutes", 15),
            avoid_close_min = sig.get("avoid_last_minutes", 15),
        ),
        lambda: check_min_score(
            score,
            min_score = sig.get("min_signal_score", 0.60),
        ),
        lambda: check_min_atr(bar, min_atr_pct=risk.get("min_atr_pct", 0.005)),
        lambda: check_volume(bar),
        lambda: check_direction_consistency(bar, direction),
        lambda: check_risk_reward(
            entry, stop, target,
            min_rr = risk.get("stop_loss_atr_mult", 1.0),  # R:R >= stop multiplier
        ),
    ]

    for rule in rules:
        blocked, reason = rule()
        if blocked:
            return True, reason

    return False, ""
