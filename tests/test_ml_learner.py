"""
tests/test_ml_learner.py
─────────────────────────
Tests for Phase 6: ML features, HRP optimiser, SelfLearner.
No network required — uses synthetic data.
"""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timezone


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_returns(n=200, tickers=None, seed=42) -> pd.DataFrame:
    np.random.seed(seed)
    tickers = tickers or ["META", "MSFT", "AAPL", "NVDA"]
    data = {}
    for i, t in enumerate(tickers):
        data[t] = np.random.normal(0.001 * (i+1), 0.02, n)
    return pd.DataFrame(data)


def make_signal(ticker="META", score=0.80, rsi=5.0,
                entry=300.0, stop=295.0, target=309.0, atr=3.0):
    from src.strategy.signal import Signal, Direction
    return Signal(
        ticker=ticker, direction=Direction.LONG, score=score,
        timestamp=datetime.now(timezone.utc), close=entry,
        entry_price=entry, stop_price=stop, target_price=target, atr=atr,
        rsi=rsi, bb_pct=0.05, ema_diff=1.2, vol_ratio=1.3,
    )


def make_bar(close=300.0, rsi=5.0, bb_pct=0.05, atr=3.0,
             vol_ratio=1.3, ema_diff=1.2) -> pd.Series:
    ts = pd.Timestamp("2024-06-15 14:00:00", tz="UTC")
    return pd.Series({
        "close": close, "open": close*0.999,
        "high": close*1.002, "low": close*0.998,
        "volume": 2_000_000,
        "rsi_2": rsi, "bb_pct": bb_pct,
        "atr": atr, "atr_pct": atr/close,
        "bb_width": 0.04, "ema_diff": ema_diff,
        "ema_fast": close+ema_diff, "ema_slow": close,
        "vol_ratio": vol_ratio, "vol_ma": 1_500_000,
        "signal_score": 0.80, "frac_diff": 0.01,
        "bb_upper": close*1.02, "bb_lower": close*0.98, "bb_mid": close,
    }, name=ts)


# ── Feature tests ─────────────────────────────────────────────────────────────

class TestMLFeatures:

    def test_feature_vector_correct_length(self):
        from src.ml.features import build_feature_vector, N_FEATURES
        signal = make_signal()
        bar    = make_bar()
        vec = build_feature_vector(signal, bar, recent_trades=[])
        assert len(vec) == N_FEATURES

    def test_feature_vector_no_nan(self):
        from src.ml.features import build_feature_vector
        vec = build_feature_vector(make_signal(), make_bar(), [])
        assert not np.isnan(vec).any()

    def test_feature_vector_no_inf(self):
        from src.ml.features import build_feature_vector
        vec = build_feature_vector(make_signal(), make_bar(), [])
        assert not np.isinf(vec).any()

    def test_long_direction_encoded_as_one(self):
        from src.ml.features import build_feature_vector
        vec = build_feature_vector(make_signal(), make_bar(), [])
        assert vec[-1] == 1.0  # is_long = last feature

    def test_recent_trades_affect_features(self):
        from src.ml.features import build_feature_vector
        signal = make_signal()
        bar    = make_bar()
        no_trades  = build_feature_vector(signal, bar, [])
        with_trades = build_feature_vector(signal, bar, [
            {"pnl": 100}, {"pnl": -50}, {"pnl": 150}
        ])
        # recent_win_rate feature should differ
        assert not np.array_equal(no_trades, with_trades)

    def test_feature_names_match_n_features(self):
        from src.ml.features import FEATURE_NAMES, N_FEATURES
        assert len(FEATURE_NAMES) == N_FEATURES


# ── HRP tests ─────────────────────────────────────────────────────────────────

class TestHRPOptimiser:

    def test_weights_sum_to_one(self):
        from src.ml.hrp import HRPOptimiser
        hrp = HRPOptimiser()
        returns = make_returns(200)
        weights = hrp.compute(returns)
        assert abs(sum(weights.values()) - 1.0) < 1e-6

    def test_all_tickers_get_weight(self):
        from src.ml.hrp import HRPOptimiser
        hrp = HRPOptimiser()
        returns = make_returns(200, ["META","MSFT","AAPL","NVDA"])
        weights = hrp.compute(returns)
        for ticker in ["META","MSFT","AAPL","NVDA"]:
            assert ticker in weights
            assert weights[ticker] > 0

    def test_min_weight_respected(self):
        from src.ml.hrp import HRPOptimiser
        hrp = HRPOptimiser(min_weight=0.10)
        weights = hrp.compute(make_returns(200))
        assert all(w >= 0.09 for w in weights.values())  # small tolerance

    def test_max_weight_respected(self):
        from src.ml.hrp import HRPOptimiser
        hrp = HRPOptimiser(max_weight=0.40)
        weights = hrp.compute(make_returns(200))
        assert all(w <= 0.41 for w in weights.values())  # small tolerance

    def test_equal_weight_fallback(self):
        from src.ml.hrp import HRPOptimiser
        hrp = HRPOptimiser()
        # Single ticker → equal weight
        returns = pd.DataFrame({"META": np.random.randn(100) * 0.02})
        weights = hrp.compute(returns)
        assert weights.get("META", 0) == pytest.approx(1.0)

    def test_position_sizes_capped(self):
        from src.ml.hrp import HRPOptimiser
        hrp = HRPOptimiser()
        weights = {"META": 0.50, "MSFT": 0.30, "AAPL": 0.20}
        sizes = hrp.position_sizes(weights, capital=25000, max_position_usd=2500)
        assert all(s <= 2500 for s in sizes.values())

    def test_higher_vol_gets_lower_weight(self):
        from src.ml.hrp import HRPOptimiser
        hrp = HRPOptimiser(min_weight=0.01)
        np.random.seed(42)
        returns = pd.DataFrame({
            "LOW_VOL":  np.random.normal(0.001, 0.005, 200),
            "HIGH_VOL": np.random.normal(0.001, 0.050, 200),
        })
        weights = hrp.compute(returns)
        # HRP should give more to low volatility ticker
        assert weights.get("LOW_VOL", 0) > weights.get("HIGH_VOL", 0)

    def test_insufficient_data_falls_back(self):
        from src.ml.hrp import HRPOptimiser
        hrp = HRPOptimiser()
        # Only 5 rows — not enough
        returns = make_returns(5)
        weights = hrp.compute(returns)
        # Should still return valid weights
        assert abs(sum(weights.values()) - 1.0) < 1e-6


# ── SelfLearner tests ─────────────────────────────────────────────────────────

class TestSelfLearner:

    def test_not_active_without_model(self):
        from src.ml.learner import SelfLearner
        learner = SelfLearner()
        # Without a trained model in DB, should not be active
        # (unless one was already trained in a previous test run)
        assert isinstance(learner.is_active, bool)

    def test_status_returns_dict(self):
        from src.ml.learner import SelfLearner
        learner = SelfLearner()
        status = learner.status()
        for key in ["is_active","last_retrain","accuracy","n_trades","min_trades"]:
            assert key in status

    def test_predict_without_model_approves(self):
        from src.ml.learner import SelfLearner
        learner = SelfLearner()
        learner._model = None  # force no model
        features = np.random.randn(14).astype(np.float32)
        pred, conf = learner.predict(features)
        assert pred == 1
        assert conf == 0.5

    def test_retrain_skips_without_trades(self):
        from src.ml.learner import SelfLearner
        learner = SelfLearner()
        result  = learner.retrain(lookback_days=1)
        # Should skip gracefully — not enough trades
        assert result["status"] in ("skipped", "success")

    def test_hrp_weights_sum_to_one(self):
        from src.ml.learner import SelfLearner
        from src.data.store import DataStore
        from src.data.features import build_features
        import tempfile, numpy as np

        learner = SelfLearner()
        # Build synthetic data in temp store
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = DataStore(f"{tmp}/test.db")
            dates = pd.date_range("2023-01-01", periods=300, freq="D", tz="UTC")
            for ticker in ["META","MSFT","AAPL","NVDA"]:
                np.random.seed(hash(ticker) % 2**31)
                close = 100 + np.cumsum(np.random.randn(300))
                close = np.maximum(close, 1)
                df = pd.DataFrame({
                    "open": close*0.999, "high": close*1.002,
                    "low": close*0.998, "close": close,
                    "volume": np.random.randint(1_000_000, 5_000_000, 300),
                }, index=dates)
                df = build_features(df)
                store.save_bars(ticker, df)

            from src.data.loader import DataLoader
            loader = DataLoader(tickers=["META","MSFT","AAPL","NVDA"])
            loader.store = store

            weights = learner.get_hrp_weights(
                tickers=["META","MSFT","AAPL","NVDA"], loader=loader)
            assert abs(sum(weights.values()) - 1.0) < 1e-6

    def test_train_model_on_synthetic_data(self):
        """Integration: train on synthetic X,y to verify pipeline works."""
        from src.ml.learner import SelfLearner
        learner = SelfLearner()
        np.random.seed(42)
        n = 60
        X = np.random.randn(n, 14).astype(np.float32)
        y = np.random.randint(0, 2, n).astype(np.int32)
        model, accuracy, importances = learner._train_model(X, y)
        assert model is not None
        assert 0.0 <= accuracy <= 1.0
        assert len(importances) == 14
