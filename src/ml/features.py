"""
src/ml/features.py
───────────────────
Feature engineering for the ML meta-labeler.

Takes a Signal + its bar context and produces a flat feature vector
that the RandomForest can learn from.

Features are designed to capture WHEN RSI(2) reversals actually work:
  - Market regime (trending vs ranging)
  - Signal strength and quality
  - Volatility context
  - Volume confirmation
  - Recent win rate (momentum of the strategy itself)

López de Prado (Advances in FM, Ch. 3):
  "The meta-labeler learns to distinguish high-quality
   from low-quality primary signals."
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ── Feature names (same order as _build_vector) ───────────────────────────────
FEATURE_NAMES = [
    # Signal quality
    "signal_score",        # composite score 0-1
    "rsi_2",               # raw RSI value
    "bb_pct",              # Bollinger Band position
    "ema_diff_pct",        # EMA diff normalised by price
    "vol_ratio",           # volume vs 20-bar average

    # Volatility context
    "atr_pct",             # ATR as % of price
    "bb_width",            # band width (regime indicator)

    # Risk-reward
    "risk_reward",         # target/stop ratio
    "risk_per_share_pct",  # risk as % of entry price

    # Trend context
    "price_vs_ema20",      # close / EMA(20) — above=uptrend
    "price_vs_ema50",      # close / EMA(50) — longer trend

    # Recent strategy performance
    "recent_win_rate",     # win rate of last 10 trades
    "recent_expectancy",   # avg P&L of last 10 trades (normalised)

    # Direction
    "is_long",             # 1=LONG, 0=SHORT
]

N_FEATURES = len(FEATURE_NAMES)


def build_feature_vector(
    signal,                    # Signal object
    bar: pd.Series,            # feature bar from DataLoader
    recent_trades: list[dict], # last N closed trades
) -> np.ndarray:
    """
    Build a flat feature vector for one signal.

    Args:
        signal       : Signal from SignalEngine
        bar          : latest feature bar (from DataLoader.get_latest)
        recent_trades: last 10-20 closed trades for performance features

    Returns:
        numpy array of shape (N_FEATURES,)
    """
    close = float(bar.get("close", 1.0))
    if close <= 0:
        close = 1.0

    # EMA context
    ema20 = float(bar.get("ema_slow", close))
    ema50 = float(bar.get("ema_slow", close))  # approximate with ema_slow

    # Recent strategy performance features
    recent_wr, recent_exp = _recent_performance(recent_trades)

    vector = [
        # Signal quality
        float(signal.score),
        float(signal.rsi),
        float(signal.bb_pct),
        float(signal.ema_diff) / close if close > 0 else 0.0,
        float(signal.vol_ratio),

        # Volatility context
        float(bar.get("atr_pct", 0.01)),
        float(bar.get("bb_width", 0.02)),

        # Risk-reward
        float(signal.risk_reward),
        float(signal.risk_per_share) / close if close > 0 else 0.0,

        # Trend context
        close / ema20 if ema20 > 0 else 1.0,
        close / ema50 if ema50 > 0 else 1.0,

        # Recent performance
        recent_wr,
        recent_exp / close if close > 0 else 0.0,  # normalised

        # Direction
        1.0 if signal.direction.value == "LONG" else 0.0,
    ]

    return np.array(vector, dtype=np.float32)


def build_dataset(
    trades: list[dict],
    bars: dict[str, pd.DataFrame],  # {ticker: full DataFrame}
    signals_history: list[dict],     # stored signal dicts at trade entry
) -> tuple[np.ndarray, np.ndarray]:
    """
    Build training dataset from historical trade outcomes.

    For each closed trade, reconstruct the signal/bar context at entry
    and label it 1 (win) or 0 (loss).

    Args:
        trades          : closed trade dicts with pnl
        bars            : historical DataFrames for all tickers
        signals_history : signal dicts stored at trade entry time

    Returns:
        X : feature matrix (n_trades, N_FEATURES)
        y : labels (n_trades,) — 1=win, 0=loss
    """
    X_rows = []
    y_rows = []

    sig_map = {s.get("entry_time", ""): s for s in signals_history}

    for trade in trades:
        if trade.get("pnl") is None:
            continue

        # Get the bar at entry time
        ticker     = trade["ticker"]
        entry_time = trade.get("entry_time", "")
        df         = bars.get(ticker)

        if df is None or df.empty:
            continue

        # Find the bar closest to entry time
        try:
            bar = _get_bar_at(df, entry_time)
        except Exception:
            continue

        # Reconstruct signal context from stored data
        sig_data = sig_map.get(entry_time, {})
        features = _features_from_stored(sig_data, bar)

        label = 1 if trade["pnl"] > 0 else 0

        X_rows.append(features)
        y_rows.append(label)

    if not X_rows:
        return np.array([]).reshape(0, N_FEATURES), np.array([])

    return np.array(X_rows, dtype=np.float32), np.array(y_rows, dtype=np.int32)


# ── Private helpers ───────────────────────────────────────────────────────────

def _recent_performance(trades: list[dict], n: int = 10) -> tuple[float, float]:
    """Compute win rate and avg P&L from last N trades."""
    recent = [t for t in trades[-n:] if t.get("pnl") is not None]
    if not recent:
        return 0.44, 0.0  # prior
    wins = [t["pnl"] for t in recent if t["pnl"] > 0]
    win_rate = len(wins) / len(recent)
    avg_pnl  = sum(t["pnl"] for t in recent) / len(recent)
    return win_rate, avg_pnl


def _get_bar_at(df: pd.DataFrame, time_str: str) -> pd.Series:
    """Get the DataFrame row closest to a timestamp string."""
    target = pd.Timestamp(time_str)
    if target.tzinfo is None:
        target = target.tz_localize("UTC")
    else:
        target = target.tz_convert("UTC")

    # Find nearest bar
    idx = df.index.get_indexer([target], method="nearest")[0]
    return df.iloc[idx]


def _features_from_stored(sig_data: dict, bar: pd.Series) -> np.ndarray:
    """Build features from stored signal dict + bar."""
    close = float(bar.get("close", 1.0)) or 1.0
    ema20 = float(bar.get("ema_slow", close)) or close

    return np.array([
        float(sig_data.get("signal_score", 0.70)),
        float(sig_data.get("rsi_2",         10.0)),
        float(sig_data.get("bb_pct",          0.05)),
        float(sig_data.get("ema_diff",         0.0)) / close,
        float(sig_data.get("vol_ratio",        1.0)),
        float(bar.get("atr_pct",               0.01)),
        float(bar.get("bb_width",              0.02)),
        float(sig_data.get("risk_reward",       1.5)),
        float(sig_data.get("atr",              2.0)) / close,
        close / ema20,
        close / ema20,
        0.44,   # prior win rate
        0.0,    # prior expectancy
        1.0,    # assume LONG
    ], dtype=np.float32)
