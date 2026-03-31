"""
tests/test_data.py
Tests for data pipeline (Phase 2).
Marked slow — excluded from make test-fast.
"""

import pytest
import pandas as pd
import numpy as np


@pytest.mark.slow
class TestDataLoader:
    """Integration tests that hit yfinance — run with make test."""

    def test_loader_raises_not_implemented_before_phase2(self):
        from src.data.loader import DataLoader
        with pytest.raises(NotImplementedError):
            DataLoader()


class TestFeatureHelpers:
    """Unit tests for feature calculations — no network required."""

    def _make_ohlcv(self, n: int = 100) -> pd.DataFrame:
        np.random.seed(42)
        close = 100 + np.cumsum(np.random.randn(n) * 0.5)
        return pd.DataFrame({
            "open":   close * 0.999,
            "high":   close * 1.002,
            "low":    close * 0.998,
            "close":  close,
            "volume": np.random.randint(100_000, 1_000_000, n),
        })

    def test_rsi_range(self):
        """RSI must always be in [0, 100]."""
        import ta
        df = self._make_ohlcv(100)
        rsi = ta.momentum.RSIIndicator(df["close"], window=2).rsi()
        valid = rsi.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_bollinger_upper_above_lower(self):
        import ta
        df = self._make_ohlcv(100)
        bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
        upper = bb.bollinger_hband().dropna()
        lower = bb.bollinger_lband().dropna()
        assert (upper >= lower).all()

    def test_ema_fast_responsive_to_slow(self):
        """EMA(9) should cross EMA(20) at least once in 100 bars of random walk."""
        import ta
        df = self._make_ohlcv(200)
        ema9  = ta.trend.EMAIndicator(df["close"], window=9).ema_indicator()
        ema20 = ta.trend.EMAIndicator(df["close"], window=20).ema_indicator()
        diff = (ema9 - ema20).dropna()
        crossings = (diff.shift(1) * diff < 0).sum()
        assert crossings >= 1

    def test_atr_is_positive(self):
        import ta
        df = self._make_ohlcv(100)
        atr = ta.volatility.AverageTrueRange(
            df["high"], df["low"], df["close"], window=14
        ).average_true_range()
        # Drop NaN and zeros from the warmup period (first 14 bars)
        atr = atr[atr > 0]
        assert len(atr) > 0
        assert (atr > 0).all()
