"""
src/backtest/metrics.py
───────────────────────
All performance metrics used by the backtester and dashboard.

Every function takes a list of trade dicts or a returns Series
and returns a single float (or dict for summary).

Pure functions — no side effects, trivial to test.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


TRADING_DAYS_PER_YEAR = 252


# ── Trade-level metrics ───────────────────────────────────────────────────────

def win_rate(trades: list[dict]) -> float:
    """Fraction of trades that were profitable."""
    if not trades:
        return 0.0
    winners = sum(1 for t in trades if t.get("pnl", 0) > 0)
    return round(winners / len(trades), 4)


def profit_factor(trades: list[dict]) -> float:
    """
    Gross profit / gross loss.
    > 1.0 = profitable overall. > 1.5 = solid. > 2.0 = excellent.
    """
    gross_profit = sum(t["pnl"] for t in trades if t.get("pnl", 0) > 0)
    gross_loss   = abs(sum(t["pnl"] for t in trades if t.get("pnl", 0) < 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return round(gross_profit / gross_loss, 4)


def avg_win_loss_ratio(trades: list[dict]) -> float:
    """Average winning trade / average losing trade (absolute values)."""
    wins   = [t["pnl"] for t in trades if t.get("pnl", 0) > 0]
    losses = [abs(t["pnl"]) for t in trades if t.get("pnl", 0) < 0]
    if not wins or not losses:
        return 0.0
    return round((sum(wins) / len(wins)) / (sum(losses) / len(losses)), 4)


def expectancy(trades: list[dict]) -> float:
    """
    Expected P&L per trade in dollars.
    = (win_rate × avg_win) - (loss_rate × avg_loss)
    Positive expectancy = edge exists.
    """
    if not trades:
        return 0.0
    wins   = [t["pnl"] for t in trades if t.get("pnl", 0) > 0]
    losses = [t["pnl"] for t in trades if t.get("pnl", 0) < 0]
    wr = len(wins) / len(trades)
    lr = len(losses) / len(trades)
    avg_w = sum(wins) / len(wins) if wins else 0
    avg_l = sum(losses) / len(losses) if losses else 0
    return round(wr * avg_w + lr * avg_l, 2)


def max_consecutive_losses(trades: list[dict]) -> int:
    """Longest losing streak — important for circuit breaker calibration."""
    max_streak = streak = 0
    for t in trades:
        if t.get("pnl", 0) < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return max_streak


def avg_trade_duration(trades: list[dict]) -> float:
    """Average number of bars a trade is held."""
    durations = [t.get("bars_held", 0) for t in trades if "bars_held" in t]
    return round(sum(durations) / len(durations), 1) if durations else 0.0


# ── Returns-level metrics ─────────────────────────────────────────────────────

def sharpe_ratio(returns: pd.Series, risk_free: float = 0.0) -> float:
    """
    Annualised Sharpe ratio.
    > 1.0 = acceptable, > 1.5 = good, > 2.0 = excellent.
    """
    if returns.empty or returns.std() == 0:
        return 0.0
    excess = returns - risk_free / TRADING_DAYS_PER_YEAR
    return round(
        float(excess.mean() / excess.std() * np.sqrt(TRADING_DAYS_PER_YEAR)),
        4,
    )


def sortino_ratio(returns: pd.Series, risk_free: float = 0.0) -> float:
    """
    Like Sharpe but only penalises downside volatility.
    Better metric for strategies that have occasional large wins.
    """
    if returns.empty:
        return 0.0
    excess     = returns - risk_free / TRADING_DAYS_PER_YEAR
    downside   = excess[excess < 0]
    downside_std = downside.std()
    if downside_std == 0:
        return 0.0
    return round(
        float(excess.mean() / downside_std * np.sqrt(TRADING_DAYS_PER_YEAR)),
        4,
    )


def max_drawdown(equity_curve: pd.Series) -> float:
    """
    Maximum peak-to-trough decline as a fraction.
    -0.20 = 20% drawdown from the peak. Lower is better (less negative).
    """
    if equity_curve.empty:
        return 0.0
    peak     = equity_curve.expanding().max()
    drawdown = (equity_curve - peak) / peak
    return round(float(drawdown.min()), 4)


def cagr(equity_curve: pd.Series) -> float:
    """
    Compound Annual Growth Rate.
    e.g. 0.25 = 25% per year.
    """
    if equity_curve.empty or len(equity_curve) < 2:
        return 0.0
    n_years = len(equity_curve) / TRADING_DAYS_PER_YEAR
    total_return = equity_curve.iloc[-1] / equity_curve.iloc[0]
    if total_return <= 0 or n_years <= 0:
        return 0.0
    return round(float(total_return ** (1 / n_years) - 1), 4)


def calmar_ratio(equity_curve: pd.Series) -> float:
    """
    CAGR / |Max Drawdown|.
    Measures return per unit of drawdown risk. > 1.0 is good.
    """
    mdd = max_drawdown(equity_curve)
    if mdd == 0:
        return 0.0
    return round(cagr(equity_curve) / abs(mdd), 4)


def equity_curve_from_trades(
    trades: list[dict],
    initial_capital: float = 25_000.0,
) -> pd.Series:
    """Build a daily equity curve from a list of completed trades."""
    if not trades:
        return pd.Series([initial_capital], name="equity")

    # Sort by exit time
    sorted_trades = sorted(
        [t for t in trades if t.get("exit_time") and t.get("pnl") is not None],
        key=lambda t: t["exit_time"],
    )

    equity = initial_capital
    points = [{"date": sorted_trades[0]["entry_time"], "equity": initial_capital}]

    for t in sorted_trades:
        equity += t["pnl"]
        points.append({"date": t["exit_time"], "equity": equity})

    df = pd.DataFrame(points)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")["equity"]
    return df


# ── Summary dict ──────────────────────────────────────────────────────────────

def summary(
    trades: list[dict],
    initial_capital: float = 25_000.0,
) -> dict:
    """
    Compute all metrics and return as a single dict.
    This is what the HTML report and dashboard consume.
    """
    if not trades:
        return _empty_summary(initial_capital)

    closed = [t for t in trades if t.get("pnl") is not None]
    if not closed:
        return _empty_summary(initial_capital)

    eq = equity_curve_from_trades(closed, initial_capital)
    daily_returns = eq.pct_change().dropna()
    final_equity = eq.iloc[-1] if not eq.empty else initial_capital

    return {
        # Trade stats
        "n_trades":              len(closed),
        "win_rate":              win_rate(closed),
        "profit_factor":         profit_factor(closed),
        "avg_win_loss_ratio":    avg_win_loss_ratio(closed),
        "expectancy":            expectancy(closed),
        "max_consecutive_losses":max_consecutive_losses(closed),
        "avg_trade_duration":    avg_trade_duration(closed),

        # Return stats
        "total_pnl":             round(final_equity - initial_capital, 2),
        "total_return":          round((final_equity - initial_capital) / initial_capital, 4),
        "cagr":                  cagr(eq),
        "sharpe":                sharpe_ratio(daily_returns),
        "sortino":               sortino_ratio(daily_returns),
        "max_drawdown":          max_drawdown(eq),
        "calmar":                calmar_ratio(eq),

        # Capital
        "initial_capital":       initial_capital,
        "final_equity":          round(final_equity, 2),
    }


def _empty_summary(initial_capital: float) -> dict:
    return {
        "n_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
        "avg_win_loss_ratio": 0.0, "expectancy": 0.0,
        "max_consecutive_losses": 0, "avg_trade_duration": 0.0,
        "total_pnl": 0.0, "total_return": 0.0, "cagr": 0.0,
        "sharpe": 0.0, "sortino": 0.0, "max_drawdown": 0.0,
        "calmar": 0.0, "initial_capital": initial_capital,
        "final_equity": initial_capital,
    }
