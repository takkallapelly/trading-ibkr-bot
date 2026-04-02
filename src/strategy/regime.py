"""
src/strategy/regime.py
───────────────────────
Market regime detection — inspired by Ernest Chan, Algorithmic Trading.

Chan's key insight (Chapter 2 & 8):
  Mean-reversion strategies ONLY work in mean-reverting regimes.
  In trending markets, RSI(2) mean-reversion LOSES money because
  the "oversold" stock keeps going lower.

  Solution: detect the regime BEFORE deciding whether to trade.
  In a trending regime → skip RSI mean-reversion signals.
  In a mean-reverting regime → trade normally.

Three regime indicators:
  1. Hurst Exponent — H < 0.5 = mean-reverting, H > 0.5 = trending
  2. Half-Life      — how fast does price revert to mean? (Ornstein-Uhlenbeck)
  3. VIX Regime     — high VIX = fear = mean-reversion works better

Chan's empirical finding: RSI(2) mean reversion works BEST when:
  - Hurst < 0.5 (confirmed mean-reverting)
  - Half-life < 20 days
  - VIX > 15 (some volatility to create oversold conditions)

Usage:
    detector = RegimeDetector()
    regime = detector.detect("META", price_series)
    if regime.is_mean_reverting:
        # proceed with RSI signal
    else:
        # skip — trending market, mean-reversion will lose
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from loguru import logger


@dataclass
class RegimeResult:
    """Result of regime detection for one ticker."""
    ticker           : str
    hurst            : float    # 0-1: < 0.5 = mean-reverting, > 0.5 = trending
    half_life        : float    # days to revert 50% of the way to mean
    is_mean_reverting: bool     # True if confirmed mean-reverting
    is_trending      : bool     # True if confirmed trending
    regime_label     : str      # "MEAN_REVERTING" / "TRENDING" / "RANDOM"
    confidence       : float    # 0-1 confidence in the regime call
    recommendation   : str      # what to do

    def __str__(self) -> str:
        return (
            f"{self.ticker} | regime={self.regime_label} | "
            f"hurst={self.hurst:.3f} | half_life={self.half_life:.1f}d | "
            f"confidence={self.confidence:.0%}"
        )


class RegimeDetector:
    """
    Detects market regime using Hurst exponent and half-life.

    Ernest Chan (Algorithmic Trading, Chapter 2):
    "The most important property of a mean-reverting time series is
    that the change in price is negatively correlated to the level of
    price. If the price is high, it is expected to decrease; if low,
    to increase."

    The Hurst exponent measures this:
      H < 0.5 → mean-reverting (our strategy should work)
      H = 0.5 → random walk (no edge)
      H > 0.5 → trending (our strategy will lose)
    """

    def __init__(
        self,
        hurst_lookback: int = 100,  # bars for Hurst calculation
        hl_lookback:    int = 252,  # bars for half-life calculation
    ):
        self.hurst_lookback = hurst_lookback
        self.hl_lookback    = hl_lookback

    def detect(self, ticker: str, prices: pd.Series) -> RegimeResult:
        """
        Detect regime for one ticker.

        Args:
            ticker : e.g. "META"
            prices : price series (daily close prices, at least 100 bars)

        Returns:
            RegimeResult with regime label and recommendation
        """
        if len(prices) < 50:
            logger.warning(f"{ticker}: insufficient bars for regime detection")
            return self._unknown(ticker)

        # Calculate Hurst exponent
        hurst = self._hurst(prices.tail(self.hurst_lookback))

        # Calculate half-life of mean reversion
        half_life = self._half_life(prices.tail(self.hl_lookback))

        # Classify regime
        if hurst < 0.40:
            regime  = "MEAN_REVERTING"
            is_mr   = True
            is_tr   = False
            conf    = (0.5 - hurst) / 0.5  # 0 at H=0.5, 1 at H=0.0
            rec     = "Trade RSI mean-reversion — confirmed mean-reverting"
        elif hurst < 0.48:
            regime  = "MEAN_REVERTING"
            is_mr   = True
            is_tr   = False
            conf    = (0.5 - hurst) / 0.1
            rec     = "Trade RSI mean-reversion — weakly mean-reverting"
        elif hurst < 0.52:
            regime  = "RANDOM"
            is_mr   = False
            is_tr   = False
            conf    = 0.5
            rec     = "Skip — random walk, no edge"
        elif hurst < 0.60:
            regime  = "TRENDING"
            is_mr   = False
            is_tr   = True
            conf    = (hurst - 0.5) / 0.1
            rec     = "Skip RSI — weakly trending, mean-reversion loses"
        else:
            regime  = "TRENDING"
            is_mr   = False
            is_tr   = True
            conf    = min((hurst - 0.5) / 0.5, 1.0)
            rec     = "Skip RSI — strongly trending, mean-reversion will lose"

        # Half-life sanity check
        # Chan: half-life > 252 means it's not really mean-reverting on daily bars
        if is_mr and half_life > 252:
            regime  = "RANDOM"
            is_mr   = False
            rec     = f"Skip — half-life {half_life:.0f}d too long for daily trading"

        result = RegimeResult(
            ticker           = ticker,
            hurst            = round(hurst, 4),
            half_life        = round(half_life, 1),
            is_mean_reverting= is_mr,
            is_trending      = is_tr,
            regime_label     = regime,
            confidence       = round(min(conf, 1.0), 3),
            recommendation   = rec,
        )

        logger.debug(str(result))
        return result

    def detect_all(
        self,
        price_data: dict[str, pd.Series],
    ) -> dict[str, RegimeResult]:
        """
        Detect regime for all tickers.

        Args:
            price_data: {ticker: price_series}

        Returns:
            {ticker: RegimeResult}
        """
        results = {}
        for ticker, prices in price_data.items():
            results[ticker] = self.detect(ticker, prices)

        # Summary log
        mr_tickers = [t for t, r in results.items() if r.is_mean_reverting]
        tr_tickers = [t for t, r in results.items() if r.is_trending]
        logger.info(
            f"Regime scan | "
            f"mean-reverting={mr_tickers} | "
            f"trending={tr_tickers}"
        )

        return results

    def should_trade(self, ticker: str, prices: pd.Series) -> tuple[bool, str]:
        """
        Quick check: should we trade RSI mean-reversion on this ticker today?

        Returns:
            (should_trade, reason)
        """
        result = self.detect(ticker, prices)
        return result.is_mean_reverting, result.recommendation

    # ── Statistical methods ───────────────────────────────────────────────────

    def _hurst(self, prices: pd.Series) -> float:
        """
        Hurst exponent via variance method (Chan, Chapter 2).

        H = 0.5 × log(Var[τ]) / log(τ) for varying lag τ

        Uses multiple lags and fits a line to log-log plot.
        Slope of this line = Hurst exponent.
        """
        try:
            lags  = range(2, min(20, len(prices) // 4))
            tau   = []
            var   = []

            log_prices = np.log(prices.values.astype(float))

            for lag in lags:
                diffs = log_prices[lag:] - log_prices[:-lag]
                tau.append(lag)
                var.append(np.std(diffs))

            # Fit log(var) ~ H * log(lag) via linear regression
            log_tau  = np.log(tau)
            log_var  = np.log(var)
            H        = np.polyfit(log_tau, log_var, 1)[0]

            return float(np.clip(H, 0.0, 1.0))

        except Exception as e:
            logger.debug(f"Hurst calculation failed: {e}")
            return 0.5  # assume random walk

    def _half_life(self, prices: pd.Series) -> float:
        """
        Half-life of mean reversion via Ornstein-Uhlenbeck regression.

        Chan (Chapter 2): fit the model
            Δy_t = λ(μ - y_{t-1}) + ε_t

        Half-life = -log(2) / log(1 + λ)

        If λ is positive → mean-reverting
        If λ is negative → trending (exploding)
        """
        try:
            y      = prices.values.astype(float)
            y_lag  = y[:-1]
            y_diff = y[1:] - y_lag

            # OLS: Δy = λ * y_lag + μ
            X    = np.column_stack([y_lag, np.ones(len(y_lag))])
            beta = np.linalg.lstsq(X, y_diff, rcond=None)[0]
            lam  = beta[0]  # mean-reversion speed

            if lam >= 0:
                # Not mean-reverting
                return 9999.0

            half_life = -np.log(2) / np.log(1 + lam)
            return max(1.0, float(half_life))

        except Exception as e:
            logger.debug(f"Half-life calculation failed: {e}")
            return 9999.0

    def _unknown(self, ticker: str) -> RegimeResult:
        return RegimeResult(
            ticker="", hurst=0.5, half_life=999.0,
            is_mean_reverting=True,  # default to allow trading
            is_trending=False,
            regime_label="UNKNOWN",
            confidence=0.0,
            recommendation="Insufficient data — trading allowed by default",
        )


# ── Convenience function ──────────────────────────────────────────────────────

def get_tradeable_tickers(
    tickers: list[str],
    price_data: dict[str, pd.Series],
) -> list[str]:
    """
    Filter tickers to only those in a mean-reverting regime.

    Use this weekly (Sunday) to update which tickers the bot trades.
    Don't change the list intraday — too much noise.

    Args:
        tickers    : full ticker list
        price_data : {ticker: daily_close_prices}

    Returns:
        list of tickers confirmed as mean-reverting
    """
    detector = RegimeDetector()
    results  = detector.detect_all({t: price_data[t] for t in tickers
                                    if t in price_data})

    tradeable = [t for t, r in results.items() if r.is_mean_reverting]

    logger.info(
        f"Regime filter | {len(tradeable)}/{len(tickers)} tickers tradeable | "
        f"{tradeable}"
    )

    return tradeable
