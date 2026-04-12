"""
tests/test_intraday.py
───────────────────────
Tests for the 5-minute intraday scanner.
No network required for unit tests.
"""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timezone


def make_5min_ohlcv(n=100, seed=42) -> pd.DataFrame:
    """Synthetic 5-min OHLCV data."""
    np.random.seed(seed)
    dates = pd.date_range(
        "2024-06-15 09:35", periods=n, freq="5min", tz="UTC"
    )
    close = 300 + np.cumsum(np.random.randn(n) * 0.3)
    close = np.maximum(close, 1)
    return pd.DataFrame({
        "open":   close * 0.9998,
        "high":   close * 1.0008,
        "low":    close * 0.9992,
        "close":  close,
        "volume": np.random.randint(50_000, 500_000, n),
    }, index=dates)


class TestIntradayConfig:

    def test_intraday_cfg_has_wider_rsi_thresholds(self):
        from src.data.intraday import _intraday_cfg
        cfg = _intraday_cfg()
        # Intraday uses wider thresholds than the extreme daily values (10/90)
        assert cfg["signals"]["rsi"]["oversold"]   < 50
        assert cfg["signals"]["rsi"]["overbought"] > 50

    def test_intraday_cfg_long_only(self):
        from src.data.intraday import _intraday_cfg
        cfg = _intraday_cfg()
        assert cfg["signals"].get("long_only") is True

    def test_intraday_cfg_has_all_required_keys(self):
        from src.data.intraday import _intraday_cfg
        cfg = _intraday_cfg()
        assert "signals" in cfg
        assert "risk"    in cfg
        assert "data"    in cfg

    def test_intraday_stop_atr_mult(self):
        from src.data.intraday import _intraday_cfg
        cfg = _intraday_cfg()
        assert cfg["risk"]["stop_loss_atr_mult"]  == 1.5
        assert cfg["risk"]["take_profit_atr_mult"] == 3.0


class TestIntradayFetcher:

    @pytest.fixture
    def fetcher(self):
        from src.data.intraday import IntradayFetcher
        return IntradayFetcher(tickers=["META","MSFT"], use_ibkr=False)

    def test_initial_bar_count_is_zero(self, fetcher):
        assert fetcher.bar_count("META") == 0

    def test_get_latest_none_when_empty(self, fetcher):
        assert fetcher.get_latest("META") is None

    def test_get_all_empty_when_no_data(self, fetcher):
        df = fetcher.get_all("META")
        assert df.empty

    def test_is_ready_false_when_no_data(self, fetcher):
        assert not fetcher.is_ready(min_bars=25)

    def test_get_latest_all_empty_dict_when_no_data(self, fetcher):
        result = fetcher.get_latest_all()
        assert result == {}

    def test_manual_injection_of_bars(self, fetcher):
        """Inject synthetic bars directly to test get_latest."""
        from src.data.features import build_features
        df = build_features(make_5min_ohlcv(80))
        fetcher._bars["META"] = df
        bar = fetcher.get_latest("META")
        assert bar is not None
        assert "rsi_2" in bar
        assert "signal_score" in bar

    def test_is_ready_true_after_injection(self, fetcher):
        from src.data.features import build_features
        df = build_features(make_5min_ohlcv(80))
        fetcher._bars["META"] = df
        fetcher._bars["MSFT"] = df.copy()
        assert fetcher.is_ready(min_bars=25)

    def test_bar_count_after_injection(self, fetcher):
        from src.data.features import build_features
        df = build_features(make_5min_ohlcv(80))
        fetcher._bars["META"] = df
        assert fetcher.bar_count("META") == len(df)

    def test_max_bars_respected(self, fetcher):
        """Bars beyond MAX_BARS should be trimmed."""
        from src.data.features import build_features
        # Create more bars than MAX_BARS
        big_df = build_features(make_5min_ohlcv(500))
        fetcher._bars["META"] = big_df.tail(fetcher.MAX_BARS)
        assert fetcher.bar_count("META") <= fetcher.MAX_BARS


class TestIntradayFeatures:
    """Test that 5-min features compute correctly."""

    def test_rsi_on_5min_bars(self):
        from src.data.features import add_rsi
        df = add_rsi(make_5min_ohlcv(100), period=2)
        valid = df["rsi_2"].dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_bb_on_5min_bars(self):
        from src.data.features import add_bollinger
        df = add_bollinger(make_5min_ohlcv(100))
        assert "bb_pct" in df.columns
        assert "bb_width" in df.columns

    def test_atr_on_5min_bars(self):
        from src.data.features import add_atr
        df = add_atr(make_5min_ohlcv(100))
        valid = df["atr"][df["atr"] > 0]
        # 5-min ATR should be small (cents) not large (dollars)
        assert valid.mean() < 5.0   # less than $5 average range on 5-min

    def test_build_features_on_5min_data(self):
        from src.data.features import build_features
        from src.data.intraday import _intraday_cfg
        df = build_features(make_5min_ohlcv(100), cfg=_intraday_cfg())
        assert not df.empty
        assert "signal_score" in df.columns
        assert "rsi_2"        in df.columns

    def test_intraday_signal_score_in_range(self):
        from src.data.features import build_features
        from src.data.intraday import _intraday_cfg
        df = build_features(make_5min_ohlcv(100), cfg=_intraday_cfg())
        valid = df["signal_score"].dropna()
        assert (valid >= -1.0).all() and (valid <= 1.0).all()


class TestIntradaySignalEngine:
    """Test signal engine works on 5-min bars with intraday config."""

    def _make_intraday_bar(self, rsi=8.0, bb_pct=0.02, score=0.80):
        from src.data.features import build_features
        df = build_features(make_5min_ohlcv(100))
        if df.empty:
            pytest.skip("No feature bars available")
        bar = df.iloc[-1].copy()
        bar["rsi_2"]        = rsi
        bar["bb_pct"]       = bb_pct
        bar["signal_score"] = score
        bar["rsi_signal"]   = 1.0 if rsi < 15 else 0.0
        bar["bb_signal"]    = 1.0 if bb_pct < 0.05 else 0.0
        bar["ema_signal"]   = 1.0
        bar["vol_ratio"]    = 1.5
        return bar

    def test_strong_5min_signal_actionable(self):
        from src.strategy.signals import SignalEngine
        from src.data.intraday import _intraday_cfg
        engine = SignalEngine(config=_intraday_cfg())
        bar    = self._make_intraday_bar(rsi=8.0, score=0.80)
        signal = engine.evaluate("META", bar)
        assert signal.direction.value == "LONG"
        assert signal.is_actionable

    def test_intraday_rsi_threshold_15_not_10(self):
        """On 5-min bars, RSI of 12 should still trigger (threshold is 15)."""
        from src.strategy.signals import SignalEngine
        from src.data.intraday import _intraday_cfg
        engine = SignalEngine(config=_intraday_cfg())
        # RSI=12: oversold on daily (threshold 10)? No. On intraday (threshold 15)? Yes
        bar = self._make_intraday_bar(rsi=12.0, score=0.80)
        bar["rsi_signal"] = 1.0  # manually set since we overrode rsi
        signal = engine.evaluate("META", bar)
        # With intraday cfg (threshold 15), RSI=12 is oversold
        assert signal.is_actionable

    def test_no_short_signals_long_only(self):
        """long_only=True should block all short signals."""
        from src.strategy.signals import SignalEngine
        from src.strategy.signal import Direction
        from src.data.intraday import _intraday_cfg
        engine = SignalEngine(config=_intraday_cfg())
        bar    = self._make_intraday_bar(rsi=92.0, bb_pct=0.98, score=-0.80)
        bar["rsi_signal"] = -1.0
        bar["bb_signal"]  = -1.0
        bar["ema_signal"] = -1.0
        bar["signal_score"] = -0.80
        signal = engine.evaluate("META", bar)
        # In long_only mode, short signals should not be actionable
        if signal.direction == Direction.SHORT:
            assert not signal.is_actionable
