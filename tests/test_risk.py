"""
tests/test_risk.py
Unit tests for the risk management layer (Phase 5).
All tests use simple math — no market data required.
"""

import pytest


class TestKellyCriterion:
    """
    Full Kelly = win_rate - (loss_rate / win_loss_ratio)
    Half-Kelly = Full Kelly × 0.5
    """

    def _kelly(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 0.0
        win_loss_ratio = avg_win / avg_loss
        loss_rate = 1 - win_rate
        full_kelly = win_rate - (loss_rate / win_loss_ratio)
        return max(0.0, full_kelly * 0.5)  # half-Kelly, never negative

    def test_kelly_positive_edge(self):
        f = self._kelly(win_rate=0.60, avg_win=150, avg_loss=100)
        assert f > 0

    def test_kelly_no_edge_is_zero(self):
        """50% win rate, equal win/loss → zero Kelly."""
        f = self._kelly(win_rate=0.50, avg_win=100, avg_loss=100)
        assert f == pytest.approx(0.0, abs=1e-9)

    def test_kelly_never_negative(self):
        """Negative edge should return 0, not negative position."""
        f = self._kelly(win_rate=0.30, avg_win=80, avg_loss=100)
        assert f >= 0.0

    def test_half_kelly_less_than_full(self):
        win_rate, avg_win, avg_loss = 0.60, 150, 100
        win_loss_ratio = avg_win / avg_loss
        full = win_rate - (1 - win_rate) / win_loss_ratio
        half = full * 0.5
        assert half < full

    def test_kelly_scales_with_edge(self):
        low_edge  = self._kelly(0.52, 100, 100)
        high_edge = self._kelly(0.65, 100, 100)
        assert high_edge > low_edge


class TestPositionSizing:
    """Position size = Kelly fraction × capital, capped at max_position_usd."""

    def _size(
        self,
        kelly_f: float,
        capital: float = 25_000,
        max_usd: float = 2_500,
    ) -> float:
        raw = kelly_f * capital
        return min(raw, max_usd)

    def test_size_capped_at_max(self):
        size = self._size(kelly_f=0.50)  # 50% of 25k = 12.5k → capped at 2.5k
        assert size == 2_500

    def test_size_below_cap_uses_kelly(self):
        size = self._size(kelly_f=0.05)  # 5% of 25k = 1.25k → under cap
        assert size == pytest.approx(1_250, rel=1e-6)

    def test_zero_kelly_gives_zero_size(self):
        assert self._size(kelly_f=0.0) == 0.0


class TestCircuitBreakers:

    def test_daily_drawdown_triggers_halt(self):
        capital = 25_000
        daily_pnl = -800   # -3.2% > 3% limit
        limit = 0.03
        assert abs(daily_pnl) / capital >= limit  # bot should halt

    def test_daily_drawdown_within_limit_no_halt(self):
        capital = 25_000
        daily_pnl = -700   # -2.8% < 3% limit
        limit = 0.03
        assert abs(daily_pnl) / capital < limit  # bot continues

    def test_portfolio_heat_limit(self):
        capital = 25_000
        open_exposure = 5_100   # 20.4% > 20% limit
        max_heat = 0.20
        assert open_exposure / capital > max_heat  # block new trade

    def test_portfolio_heat_allows_trade(self):
        capital = 25_000
        open_exposure = 4_900   # 19.6% < 20% limit
        max_heat = 0.20
        assert open_exposure / capital < max_heat  # allow new trade


class TestRiskManager:
    """Smoke test for the Phase 5 class import."""

    def test_risk_manager_raises_not_implemented_before_phase5(self):
        from src.risk.manager import RiskManager
        with pytest.raises(NotImplementedError):
            RiskManager()
