"""
tests/test_data_pipeline.py
────────────────────────────
Tests for Phase 2: DataLoader, features, store.
Network tests are marked slow — run with: python -m pytest tests/ -v
Pure-math and DB tests run instantly with no network needed.
"""

import pytest
import numpy as np
import pandas as pd
from pathlib import Path
import tempfile


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_ohlcv(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """Create a realistic synthetic OHLCV DataFrame."""
    np.random.seed(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC")
    close = 100 + np.cumsum(np.random.randn(n) * 0.8)
    close = np.maximum(close, 1)  # no negative prices
    return pd.DataFrame({
        "open":   close * np.random.uniform(0.998, 1.002, n),
        "high":   close * np.random.uniform(1.001, 1.005, n),
        "low":    close * np.random.uniform(0.995, 0.999, n),
        "close":  close,
        "volume": np.random.randint(500_000, 5_000_000, n),
    }, index=dates)


# ── Feature tests ─────────────────────────────────────────────────────────────

class TestRSIFeature:

    def test_rsi_column_created(self):
        from src.data.features import add_rsi
        df = add_rsi(make_ohlcv(), period=2)
        assert "rsi_2" in df.columns

    def test_rsi_in_valid_range(self):
        from src.data.features import add_rsi
        df = add_rsi(make_ohlcv(200), period=2)
        valid = df["rsi_2"].dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_rsi_not_all_nan(self):
        from src.data.features import add_rsi
        df = add_rsi(make_ohlcv(50), period=2)
        assert df["rsi_2"].notna().sum() > 0

    def test_rsi_oversold_below_10(self):
        """Force a declining price series and check RSI goes below 10."""
        from src.data.features import add_rsi
        df = make_ohlcv(50)
        # Create a strongly declining close
        df["close"] = np.linspace(100, 60, 50)
        df["high"]  = df["close"] * 1.001
        df["low"]   = df["close"] * 0.999
        df = add_rsi(df, period=2)
        assert (df["rsi_2"].dropna() < 10).any()


class TestBollingerFeature:

    def test_bb_columns_created(self):
        from src.data.features import add_bollinger
        df = add_bollinger(make_ohlcv())
        for col in ["bb_upper", "bb_lower", "bb_mid", "bb_pct", "bb_width"]:
            assert col in df.columns

    def test_upper_always_above_lower(self):
        from src.data.features import add_bollinger
        df = add_bollinger(make_ohlcv()).dropna()
        assert (df["bb_upper"] >= df["bb_lower"]).all()

    def test_bb_pct_range(self):
        from src.data.features import add_bollinger
        df = add_bollinger(make_ohlcv()).dropna()
        # bb_pct can go outside 0-1 in extreme moves, but should mostly be inside
        inside = ((df["bb_pct"] >= -0.5) & (df["bb_pct"] <= 1.5)).mean()
        assert inside > 0.95

    def test_bb_width_positive(self):
        from src.data.features import add_bollinger
        df = add_bollinger(make_ohlcv()).dropna()
        assert (df["bb_width"] > 0).all()


class TestEMAFeature:

    def test_ema_columns_created(self):
        from src.data.features import add_ema
        df = add_ema(make_ohlcv())
        for col in ["ema_fast", "ema_slow", "ema_diff", "ema_cross"]:
            assert col in df.columns

    def test_ema_cross_values(self):
        from src.data.features import add_ema
        df = add_ema(make_ohlcv(200))
        assert set(df["ema_cross"].unique()).issubset({-1, 0, 1})

    def test_ema_cross_detected(self):
        from src.data.features import add_ema
        df = add_ema(make_ohlcv(200))
        assert df["ema_cross"].abs().sum() >= 1


class TestATRFeature:

    def test_atr_columns_created(self):
        from src.data.features import add_atr
        df = add_atr(make_ohlcv())
        assert "atr" in df.columns
        assert "atr_pct" in df.columns

    def test_atr_positive_after_warmup(self):
        from src.data.features import add_atr
        df = add_atr(make_ohlcv(100))
        valid = df["atr"][df["atr"] > 0]
        assert len(valid) > 0
        assert (valid > 0).all()

    def test_atr_pct_reasonable(self):
        from src.data.features import add_atr
        df = add_atr(make_ohlcv(100)).dropna()
        df = df[df["atr"] > 0]
        # ATR% should be between 0% and 20% for normal stocks
        assert (df["atr_pct"] > 0).all()
        assert (df["atr_pct"] < 0.20).all()


class TestFractionalDiff:

    def test_output_length_matches_input(self):
        from src.data.features import fractional_diff
        series = pd.Series(np.random.randn(200) + 100)
        result = fractional_diff(series, d=0.4)
        assert len(result) <= len(series)

    def test_result_is_more_stationary(self):
        """Fractionally diffed series should have smaller mean than raw price."""
        from src.data.features import fractional_diff
        np.random.seed(0)
        series = pd.Series(100 + np.cumsum(np.random.randn(300)))
        result = fractional_diff(series, d=0.4).dropna()
        # Raw price mean ~100-150, frac diff should be near 0
        assert abs(result.mean()) < abs(series.mean())

    def test_frac_diff_column_added(self):
        from src.data.features import add_frac_diff
        df = add_frac_diff(make_ohlcv(200))
        assert "frac_diff" in df.columns


class TestSignalScore:

    def test_signal_score_column_created(self):
        from src.data.features import add_rsi, add_bollinger, add_ema, add_signal_score
        df = make_ohlcv(200)
        df = add_rsi(df)
        df = add_bollinger(df)
        df = add_ema(df)
        df = add_signal_score(df)
        assert "signal_score" in df.columns

    def test_signal_score_in_range(self):
        from src.data.features import add_rsi, add_bollinger, add_ema, add_signal_score
        df = make_ohlcv(200)
        df = add_rsi(df)
        df = add_bollinger(df)
        df = add_ema(df)
        df = add_signal_score(df)
        valid = df["signal_score"].dropna()
        assert (valid >= -1.0).all() and (valid <= 1.0).all()


class TestBuildFeatures:

    def test_build_features_runs_without_error(self):
        from src.data.features import build_features
        df = build_features(make_ohlcv(300))
        assert not df.empty

    def test_build_features_expected_columns(self):
        from src.data.features import build_features
        df = build_features(make_ohlcv(300))
        expected = ["rsi_2", "bb_upper", "ema_fast", "atr", "signal_score", "frac_diff"]
        for col in expected:
            assert col in df.columns, f"Missing column: {col}"

    def test_build_features_no_nan_in_key_cols(self):
        from src.data.features import build_features
        df = build_features(make_ohlcv(300))
        for col in ["rsi_2", "signal_score", "atr"]:
            assert df[col].isna().sum() == 0, f"{col} has NaN after dropna"


# ── DataStore tests ───────────────────────────────────────────────────────────

class TestDataStore:

    @pytest.fixture
    def store(self, tmp_path):
        from src.data.store import DataStore
        return DataStore(db_path=tmp_path / "test.db")

    def test_save_and_load_bars(self, store):
        from src.data.features import build_features
        df = build_features(make_ohlcv(300))
        store.save_bars("TSLA", df)
        loaded = store.load_bars("TSLA")
        assert len(loaded) == len(df)

    def test_bar_count(self, store):
        from src.data.features import build_features
        df = build_features(make_ohlcv(300))
        store.save_bars("NVDA", df)
        assert store.bar_count("NVDA") == len(df)

    def test_bar_count_zero_for_unknown_ticker(self, store):
        assert store.bar_count("UNKNOWN") == 0

    def test_load_bars_empty_for_unknown_ticker(self, store):
        df = store.load_bars("UNKNOWN")
        assert df.empty

    def test_log_and_load_trade(self, store):
        trade = {
            "ticker": "TSLA",
            "side": "LONG",
            "entry_time": "2024-01-15 10:30:00",
            "entry_price": 250.50,
            "qty": 10,
            "stop_price": 245.00,
            "target_price": 258.00,
            "signal_score": 0.75,
        }
        trade_id = store.log_trade(trade)
        assert trade_id > 0

        trades = store.load_trades(closed_only=False)
        assert len(trades) == 1
        assert trades.iloc[0]["ticker"] == "TSLA"

    def test_update_trade(self, store):
        trade = {
            "ticker": "NVDA", "side": "LONG",
            "entry_time": "2024-01-15 10:30:00",
            "entry_price": 500.0, "qty": 5,
        }
        trade_id = store.log_trade(trade)
        store.update_trade(trade_id, {
            "exit_time": "2024-01-15 14:00:00",
            "exit_price": 510.0,
            "pnl": 50.0,
            "pnl_pct": 0.02,
            "exit_reason": "TAKE_PROFIT",
        })
        trades = store.load_trades(closed_only=True)
        assert len(trades) == 1
        assert trades.iloc[0]["pnl"] == 50.0

    def test_equity_snapshot(self, store):
        store.save_equity_snapshot(equity=25500.0, daily_pnl=500.0, open_positions=2)
        curve = store.load_equity_curve()
        assert len(curve) == 1
        assert curve.iloc[0]["equity"] == 25500.0

    def test_win_rate_empty_is_zero(self, store):
        assert store.win_rate() == 0.0


# ── DataLoader integration test ───────────────────────────────────────────────

@pytest.mark.slow
class TestDataLoaderIntegration:

    def test_fetch_single_ticker(self, tmp_path):
        """Downloads real AAPL data — requires internet."""
        from src.data.loader import DataLoader
        from src.data.store import DataStore

        store = DataStore(db_path=tmp_path / "test.db")
        loader = DataLoader(
            tickers=["AAPL"],
            interval="1d",
            use_cache=False,
        )
        loader.store = store
        results = loader.fetch_all(start="2024-01-01", end="2024-06-01")

        assert "AAPL" in results
        df = results["AAPL"]
        assert not df.empty
        assert "rsi_2" in df.columns
        assert "signal_score" in df.columns
        assert len(df) > 50

    def test_get_latest_signal_structure(self, tmp_path):
        """Checks get_latest_signal returns expected keys."""
        from src.data.loader import DataLoader
        from src.data.store import DataStore
        from src.data.features import build_features

        store = DataStore(db_path=tmp_path / "test.db")
        df = build_features(make_ohlcv(300))
        store.save_bars("TSLA", df)

        loader = DataLoader(tickers=["TSLA"])
        loader.store = store

        signal = loader.get_latest_signal("TSLA")
        for key in ["ticker", "signal_score", "rsi_2", "bb_pct", "atr", "close"]:
            assert key in signal
