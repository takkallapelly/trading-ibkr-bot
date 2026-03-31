"""
src/data/features.py
────────────────────
Computes all technical features used by the signal engine.

Feature groups:
  1. RSI(2)          — primary reversal signal (Ernie Chan)
  2. Bollinger Bands — secondary reversal confirmation
  3. EMA 9 / 20      — momentum direction filter
  4. ATR(14)         — volatility for position sizing and stops
  5. Volume features — relative volume, volume trend
  6. Fractional differentiation — stationary price series (López de Prado)
  7. Composite signal score — weighted combination of all signals

All functions are PURE — they take a DataFrame and return a DataFrame.
No side effects, no global state. Easy to test and reuse.
"""

import numpy as np
import pandas as pd
import ta
from loguru import logger


# ── 1. RSI ────────────────────────────────────────────────────────────────────

def add_rsi(df: pd.DataFrame, period: int = 2) -> pd.DataFrame:
    """
    RSI(2) — extremely short period makes it hypersensitive to reversals.
    Values below 10 = oversold (long signal).
    Values above 90 = overbought (short signal).
    This is the primary signal from Ernie Chan's mean-reversion work.
    """
    df = df.copy()
    df[f"rsi_{period}"] = ta.momentum.RSIIndicator(
        df["close"], window=period
    ).rsi()
    return df


# ── 2. Bollinger Bands ────────────────────────────────────────────────────────

def add_bollinger(
    df: pd.DataFrame,
    period: int = 20,
    std_dev: float = 2.0,
) -> pd.DataFrame:
    """
    Bollinger Bands — price touching the lower band confirms oversold.
    Columns added:
      bb_upper, bb_lower, bb_mid  — the three bands
      bb_pct                      — where price sits within the bands (0=lower, 1=upper)
      bb_width                    — band width as % of mid (measures volatility)
    """
    df = df.copy()
    bb = ta.volatility.BollingerBands(
        df["close"], window=period, window_dev=std_dev
    )
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_mid"]   = bb.bollinger_mavg()
    df["bb_pct"]   = bb.bollinger_pband()   # 0.0 = at lower band, 1.0 = at upper band
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    return df


# ── 3. EMA Momentum ───────────────────────────────────────────────────────────

def add_ema(
    df: pd.DataFrame,
    fast: int = 9,
    slow: int = 20,
) -> pd.DataFrame:
    """
    EMA crossover — fast above slow = uptrend, below = downtrend.
    Columns added:
      ema_fast, ema_slow          — the two EMAs
      ema_diff                    — fast - slow (positive = bullish)
      ema_cross                   — +1 on bullish cross, -1 on bearish cross, 0 otherwise
    """
    df = df.copy()
    df["ema_fast"] = ta.trend.EMAIndicator(df["close"], window=fast).ema_indicator()
    df["ema_slow"] = ta.trend.EMAIndicator(df["close"], window=slow).ema_indicator()
    df["ema_diff"] = df["ema_fast"] - df["ema_slow"]

    # Detect crossover bars only
    prev_diff = df["ema_diff"].shift(1)
    df["ema_cross"] = 0
    df.loc[(df["ema_diff"] > 0) & (prev_diff <= 0), "ema_cross"] = 1   # bullish cross
    df.loc[(df["ema_diff"] < 0) & (prev_diff >= 0), "ema_cross"] = -1  # bearish cross

    return df


# ── 4. ATR (volatility) ───────────────────────────────────────────────────────

def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Average True Range — measures how much price moves on average.
    Used for: stop loss distance, take profit distance, position sizing.
    Column added:
      atr         — raw ATR value in dollars
      atr_pct     — ATR as % of close price (normalised across tickers)
    """
    df = df.copy()
    df["atr"] = ta.volatility.AverageTrueRange(
        df["high"], df["low"], df["close"], window=period
    ).average_true_range()
    df["atr_pct"] = df["atr"] / df["close"]
    return df


# ── 5. Volume features ────────────────────────────────────────────────────────

def add_volume_features(
    df: pd.DataFrame,
    lookback: int = 20,
) -> pd.DataFrame:
    """
    Volume context features (inspired by Harris — Trading and Exchanges).
    High volume on a reversal bar = more reliable signal.
    Columns added:
      vol_ma          — rolling average volume
      vol_ratio       — current volume / average volume (>1.5 = high volume)
      vol_trend       — volume trending up (+1) or down (-1)
    """
    df = df.copy()
    df["vol_ma"]    = df["volume"].rolling(window=lookback).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma"]
    df["vol_trend"] = np.sign(df["volume"].diff(5))
    return df


# ── 6. Fractional Differentiation (López de Prado) ───────────────────────────

def fractional_diff(
    series: pd.Series,
    d: float = 0.4,
    thresh: float = 1e-4,
) -> pd.Series:
    """
    Fractionally differentiated price series (Chapter 5, Advances in FM).

    The problem with raw prices: non-stationary (ML models hate this).
    The problem with returns (d=1): throws away price memory entirely.
    Fractional diff (0 < d < 1) finds the sweet spot:
      - Stationary enough for ML to learn from
      - Still carries price level memory (unlike pure returns)

    Args:
        series : raw close price series
        d      : differentiation order (0=raw prices, 1=returns, 0.4=sweet spot)
        thresh : weights below this threshold are dropped (controls memory length)

    Returns:
        Fractionally differentiated series (same index, NaNs at start)
    """
    # Compute weights
    weights = _get_weights(d, size=len(series), thresh=thresh)
    width = len(weights) - 1

    output = {}
    for i in range(width, len(series)):
        window = series.iloc[i - width: i + 1].values[::-1]
        output[series.index[i]] = float(np.dot(weights, window))

    return pd.Series(output, name=f"frac_diff_d{d}")


def _get_weights(d: float, size: int, thresh: float) -> np.ndarray:
    """Compute binomial series weights for fractional differentiation."""
    weights = [1.0]
    for k in range(1, size):
        w = -weights[-1] * (d - k + 1) / k
        if abs(w) < thresh:
            break
        weights.append(w)
    return np.array(weights[::-1])


def add_frac_diff(
    df: pd.DataFrame,
    d: float = 0.4,
) -> pd.DataFrame:
    """Add fractionally differentiated close price to DataFrame."""
    df = df.copy()
    try:
        df["frac_diff"] = fractional_diff(df["close"], d=d)
    except Exception as e:
        logger.warning(f"Fractional diff failed: {e} — skipping")
        df["frac_diff"] = np.nan
    return df


# ── 7. Composite signal score ─────────────────────────────────────────────────

def add_signal_score(
    df: pd.DataFrame,
    rsi_period: int = 2,
    rsi_oversold: float = 10.0,
    rsi_overbought: float = 90.0,
    rsi_weight: float = 0.50,
    bb_weight: float = 0.30,
    ema_weight: float = 0.20,
) -> pd.DataFrame:
    """
    Combine RSI, Bollinger Band, and EMA signals into one score.

    Score ranges from -1.0 (strong short) to +1.0 (strong long).
    Signal engine uses score > 0.60 for long, < -0.60 for short.

    Columns added:
      rsi_signal   — raw RSI signal (-1, 0, +1)
      bb_signal    — raw BB signal (-1, 0, +1)
      ema_signal   — raw EMA signal (-1, 0, +1)
      signal_score — weighted composite (-1.0 to +1.0)
    """
    df = df.copy()
    rsi_col = f"rsi_{rsi_period}"

    # ── RSI signal ────────────────────────────────────────────────────────────
    df["rsi_signal"] = 0.0
    if rsi_col in df.columns:
        df.loc[df[rsi_col] < rsi_oversold,  "rsi_signal"] =  1.0
        df.loc[df[rsi_col] > rsi_overbought,"rsi_signal"] = -1.0

    # ── Bollinger Band signal ─────────────────────────────────────────────────
    df["bb_signal"] = 0.0
    if "bb_pct" in df.columns:
        df.loc[df["bb_pct"] < 0.05, "bb_signal"] =  1.0   # near lower band
        df.loc[df["bb_pct"] > 0.95, "bb_signal"] = -1.0   # near upper band

    # ── EMA signal ────────────────────────────────────────────────────────────
    df["ema_signal"] = 0.0
    if "ema_diff" in df.columns:
        df.loc[df["ema_diff"] > 0, "ema_signal"] =  1.0   # uptrend
        df.loc[df["ema_diff"] < 0, "ema_signal"] = -1.0   # downtrend

    # ── Composite weighted score ──────────────────────────────────────────────
    df["signal_score"] = (
        df["rsi_signal"] * rsi_weight
        + df["bb_signal"] * bb_weight
        + df["ema_signal"] * ema_weight
    )

    return df


# ── Master feature builder ────────────────────────────────────────────────────

def build_features(
    df: pd.DataFrame,
    cfg: dict | None = None,
) -> pd.DataFrame:
    """
    Run all feature functions in the correct order.
    This is the single function DataLoader calls.

    Args:
        df  : raw OHLCV DataFrame
        cfg : config dict (from config.yaml). Uses defaults if None.

    Returns:
        DataFrame with all features added, NaN rows at start dropped.
    """
    if df.empty:
        return df

    c = cfg or {}
    sig = c.get("signals", {})
    risk = c.get("risk", {})
    data = c.get("data", {})

    df = add_rsi(df,
        period=sig.get("rsi", {}).get("period", 2))

    df = add_bollinger(df,
        period=sig.get("bollinger", {}).get("period", 20),
        std_dev=sig.get("bollinger", {}).get("std_dev", 2.0))

    df = add_ema(df,
        fast=sig.get("ema", {}).get("fast", 9),
        slow=sig.get("ema", {}).get("slow", 20))

    df = add_atr(df,
        period=risk.get("atr_period", 14))

    df = add_volume_features(df)

    df = add_frac_diff(df,
        d=data.get("fractional_diff_d", 0.4))

    df = add_signal_score(df,
        rsi_period=sig.get("rsi", {}).get("period", 2),
        rsi_oversold=sig.get("rsi", {}).get("oversold", 10),
        rsi_overbought=sig.get("rsi", {}).get("overbought", 90),
        rsi_weight=sig.get("rsi", {}).get("weight", 0.50),
        bb_weight=sig.get("bollinger", {}).get("weight", 0.30),
        ema_weight=sig.get("ema", {}).get("weight", 0.20))

    # Drop warmup rows where indicators haven't enough history
    before = len(df)
    df = df.dropna(subset=["rsi_2", "bb_upper", "ema_fast", "atr"])
    dropped = before - len(df)
    if dropped > 0:
        logger.debug(f"Dropped {dropped} warmup bars after feature calculation")

    return df
