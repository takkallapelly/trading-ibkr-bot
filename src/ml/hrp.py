"""
src/ml/hrp.py
──────────────
Hierarchical Risk Parity (HRP) — dynamic capital allocation
across tickers based on recent correlation and volatility.

López de Prado (Machine Learning for Asset Managers, Ch. 5):
  "HRP allocates capital inversely to risk, respecting
   the hierarchical correlation structure of assets."

Traditional equal-weight: $6,250 per ticker (25k / 4)
HRP: gives more to low-volatility, low-correlation tickers
     and less to high-volatility, correlated ones.

Why better than equal-weight:
  - META and MSFT are correlated → reduces combined allocation
  - NVDA is more volatile → gets smaller allocation
  - AAPL is less volatile → gets larger allocation
  - Rebalanced weekly → adapts to changing market regimes

Usage:
    hrp = HRPOptimiser()
    weights = hrp.compute(returns_df)
    # weights = {"META": 0.28, "MSFT": 0.31, "AAPL": 0.25, "NVDA": 0.16}
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from loguru import logger


class HRPOptimiser:
    """
    Hierarchical Risk Parity portfolio optimiser.

    Computes optimal capital allocation weights for a set of tickers
    using recent return correlations and volatilities.

    Args:
        lookback_days : days of returns to use for covariance estimation
        min_weight    : minimum allocation per ticker (prevents zeroing out)
        max_weight    : maximum allocation per ticker (prevents concentration)
    """

    def __init__(
        self,
        lookback_days: int = 60,
        min_weight: float = 0.05,
        max_weight: float = 0.50,
    ):
        self.lookback_days = lookback_days
        self.min_weight    = min_weight
        self.max_weight    = max_weight

    def compute(self, returns: pd.DataFrame) -> dict[str, float]:
        """
        Compute HRP weights from a DataFrame of daily returns.

        Args:
            returns : DataFrame where each column is a ticker's daily returns
                      Index = dates, columns = ticker symbols

        Returns:
            dict {ticker: weight} — weights sum to 1.0
        """
        if returns.empty or returns.shape[1] < 2:
            # Fall back to equal weight
            return self._equal_weight(list(returns.columns))

        # Use recent lookback window
        recent = returns.tail(self.lookback_days).dropna(how="all")

        if len(recent) < 20:
            logger.warning("HRP: insufficient data — using equal weight")
            return self._equal_weight(list(returns.columns))

        try:
            # Step 1: Compute correlation and covariance
            corr = recent.corr()
            cov  = recent.cov()

            # Step 2: Hierarchical clustering
            ordered_tickers = self._quasi_diagonalise(corr)

            # Step 3: Recursive bisection
            raw_weights = self._recursive_bisection(cov, ordered_tickers)

            # Step 4: Apply min/max constraints and renormalise
            weights = self._apply_constraints(raw_weights)

            logger.info(
                "HRP weights: "
                + " | ".join(f"{t}={w:.1%}" for t, w in sorted(weights.items()))
            )
            return weights

        except Exception as e:
            logger.warning(f"HRP failed ({e}) — using equal weight")
            return self._equal_weight(list(returns.columns))

    def compute_from_loader(
        self,
        loader,                    # DataLoader instance
        tickers: list[str],
    ) -> dict[str, float]:
        """
        Convenience: compute HRP directly from DataLoader.

        Args:
            loader  : DataLoader with data already fetched
            tickers : list of tickers to include

        Returns:
            dict {ticker: weight}
        """
        returns_data = {}
        for ticker in tickers:
            df = loader.get(ticker)
            if not df.empty and "close" in df.columns:
                returns_data[ticker] = df["close"].pct_change().dropna()

        if not returns_data:
            return self._equal_weight(tickers)

        returns_df = pd.DataFrame(returns_data).tail(self.lookback_days)
        return self.compute(returns_df)

    def position_sizes(
        self,
        weights: dict[str, float],
        capital: float,
        max_position_usd: float = 2_500.0,
    ) -> dict[str, float]:
        """
        Convert HRP weights to dollar position sizes.

        Args:
            weights          : from compute()
            capital          : total trading capital
            max_position_usd : hard cap per position

        Returns:
            dict {ticker: position_size_usd}
        """
        sizes = {}
        for ticker, weight in weights.items():
            raw_size = weight * capital
            sizes[ticker] = min(raw_size, max_position_usd)
        return sizes

    # ── Private: HRP algorithm ────────────────────────────────────────────────

    def _quasi_diagonalise(self, corr: pd.DataFrame) -> list[str]:
        """
        Order tickers by hierarchical clustering.
        Similar stocks (high correlation) end up adjacent.
        This is the key insight of HRP — treat the portfolio
        as a hierarchy, not a flat collection.
        """
        tickers = list(corr.columns)

        if len(tickers) <= 2:
            return tickers

        # Simple approach: order by average correlation (proxy for clustering)
        avg_corr = corr.mean()
        return list(avg_corr.sort_values().index)

    def _recursive_bisection(
        self,
        cov: pd.DataFrame,
        ordered: list[str],
    ) -> dict[str, float]:
        """
        Allocate capital through recursive bisection.

        Split ordered tickers into two halves, allocate proportionally
        to inverse variance of each half, then recurse.

        This gives diversification within clusters — unlike
        Markowitz which concentrates in low-variance assets.
        """
        weights = {t: 1.0 for t in ordered}

        def _allocate(items: list[str], weight: float) -> None:
            if len(items) == 1:
                weights[items[0]] = weight
                return

            # Split into two halves
            mid   = len(items) // 2
            left  = items[:mid]
            right = items[mid:]

            # Variance of each cluster
            var_left  = self._cluster_variance(cov, left)
            var_right = self._cluster_variance(cov, right)

            # Allocate inversely proportional to variance
            total = var_left + var_right
            if total == 0:
                w_left = w_right = 0.5
            else:
                w_left  = 1 - var_left  / total
                w_right = 1 - var_right / total
                # Renormalise
                w_sum   = w_left + w_right
                w_left  /= w_sum
                w_right /= w_sum

            _allocate(left,  weight * w_left)
            _allocate(right, weight * w_right)

        _allocate(ordered, 1.0)
        return weights

    def _cluster_variance(self, cov: pd.DataFrame, tickers: list[str]) -> float:
        """Variance of an equally-weighted cluster of tickers."""
        if len(tickers) == 1:
            return float(cov.loc[tickers[0], tickers[0]])

        sub_cov = cov.loc[tickers, tickers]
        w       = np.ones(len(tickers)) / len(tickers)
        return float(w @ sub_cov.values @ w)

    def _apply_constraints(self, weights: dict[str, float]) -> dict[str, float]:
        """Apply min/max weight constraints and renormalise."""
        # Clip to bounds
        clipped = {t: max(self.min_weight, min(self.max_weight, w))
                   for t, w in weights.items()}
        # Renormalise to sum = 1.0
        total = sum(clipped.values())
        if total == 0:
            return self._equal_weight(list(weights.keys()))
        return {t: w / total for t, w in clipped.items()}

    def _equal_weight(self, tickers: list[str]) -> dict[str, float]:
        """Fallback: equal weight for all tickers."""
        if not tickers:
            return {}
        w = 1.0 / len(tickers)
        return {t: w for t in tickers}
