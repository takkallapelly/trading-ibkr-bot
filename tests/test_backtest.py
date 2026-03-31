"""
tests/test_backtest.py
Unit tests for backtest metrics and purged CV logic (Phase 4).
Pure math — no market data required.
"""

import pytest
import numpy as np


class TestPerformanceMetrics:
    """Test the core metrics computed by the backtester."""

    def _returns(self) -> np.ndarray:
        np.random.seed(0)
        return np.random.normal(0.0005, 0.01, 252)  # ~252 trading days

    def _sharpe(self, returns: np.ndarray, risk_free: float = 0.0) -> float:
        excess = returns - risk_free / 252
        if excess.std() == 0:
            return 0.0
        return float(excess.mean() / excess.std() * np.sqrt(252))

    def _max_drawdown(self, returns: np.ndarray) -> float:
        equity = (1 + returns).cumprod()
        peak = np.maximum.accumulate(equity)
        dd = (equity - peak) / peak
        return float(dd.min())

    def _cagr(self, returns: np.ndarray) -> float:
        total = (1 + returns).prod()
        n_years = len(returns) / 252
        return float(total ** (1 / n_years) - 1)

    def _win_rate(self, returns: np.ndarray) -> float:
        winners = (returns > 0).sum()
        return float(winners / len(returns))

    def test_sharpe_positive_for_positive_mean(self):
        r = np.full(252, 0.001)  # constant 0.1% daily return
        assert self._sharpe(r) > 0

    def test_sharpe_zero_for_zero_returns(self):
        r = np.zeros(252)
        assert self._sharpe(r) == 0.0

    def test_max_drawdown_is_negative_or_zero(self):
        r = self._returns()
        assert self._max_drawdown(r) <= 0

    def test_max_drawdown_zero_for_always_up(self):
        r = np.full(252, 0.01)  # monotonically increasing equity
        assert self._max_drawdown(r) == pytest.approx(0.0, abs=1e-10)

    def test_cagr_positive_for_positive_returns(self):
        r = np.full(252, 0.001)
        assert self._cagr(r) > 0

    def test_win_rate_between_0_and_1(self):
        r = self._returns()
        wr = self._win_rate(r)
        assert 0 <= wr <= 1

    def test_win_rate_all_winners(self):
        r = np.full(100, 0.01)
        assert self._win_rate(r) == pytest.approx(1.0)


class TestPurgedKFold:
    """
    Verify purged CV logic: test indices must not appear in the
    embargo window adjacent to the training set.
    """

    def _purged_folds(
        self,
        n: int = 1000,
        n_splits: int = 5,
        embargo_pct: float = 0.01,
    ) -> list[tuple[list[int], list[int]]]:
        """Minimal purged k-fold implementation for testing."""
        embargo = int(n * embargo_pct)
        fold_size = n // n_splits
        folds = []
        for k in range(n_splits):
            test_start = k * fold_size
            test_end   = test_start + fold_size
            # Train = everything before (test_start - embargo) and after test_end
            train = list(range(0, max(0, test_start - embargo))) + \
                    list(range(test_end, n))
            test  = list(range(test_start, test_end))
            folds.append((train, test))
        return folds

    def test_no_overlap_between_train_and_test(self):
        for train, test in self._purged_folds():
            assert len(set(train) & set(test)) == 0

    def test_embargo_gap_respected(self):
        embargo = 10
        for train, test in self._purged_folds(n=1000, embargo_pct=0.01):
            if not train or not test:
                continue
            # No training index should fall within the embargo gap before test
            test_start = min(test)
            train_in_embargo = [t for t in train if test_start - embargo <= t < test_start]
            assert len(train_in_embargo) == 0

    def test_correct_number_of_folds(self):
        folds = self._purged_folds(n_splits=6)
        assert len(folds) == 6

    def test_each_test_fold_non_empty(self):
        for _, test in self._purged_folds():
            assert len(test) > 0

    def test_all_indices_appear_as_test_exactly_once(self):
        n = 1000
        folds = self._purged_folds(n=n, n_splits=5)
        all_test = [idx for _, test in folds for idx in test]
        # Each index 0..999 should appear in exactly one test fold
        assert sorted(all_test) == list(range(n))


class TestBacktester:
    """Smoke test for Phase 4 class."""

    def test_backtester_raises_not_implemented_before_phase4(self):
        from src.backtest.engine import Backtester
        with pytest.raises(NotImplementedError):
            Backtester()
