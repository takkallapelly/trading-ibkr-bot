import pytest
import numpy as np

class TestSignalScore:
    def _score(self, rsi_signal, bb_signal, ema_signal):
        return rsi_signal * 0.50 + bb_signal * 0.30 + ema_signal * 0.20
    def test_all_signals_aligned_gives_max(self):
        assert self._score(1.0, 1.0, 1.0) == pytest.approx(1.0)
    def test_no_signals_gives_zero(self):
        assert self._score(0, 0, 0) == 0.0
    def test_rsi_only_below_threshold(self):
        assert self._score(rsi_signal=1.0, bb_signal=0, ema_signal=0) < 0.60
    def test_rsi_plus_bb_above_threshold(self):
        assert self._score(rsi_signal=1.0, bb_signal=1.0, ema_signal=0) >= 0.60
    def test_weights_sum_to_one(self):
        assert 0.50 + 0.30 + 0.20 == pytest.approx(1.0)

class TestRSISignal:
    def _rsi_signal(self, rsi):
        if rsi < 10:  return 1.0
        if rsi > 90:  return -1.0
        return 0.0
    def test_oversold_gives_long_signal(self):
        assert self._rsi_signal(5.0) == 1.0
    def test_overbought_gives_short_signal(self):
        assert self._rsi_signal(95.0) == -1.0
    def test_neutral_rsi_gives_no_signal(self):
        assert self._rsi_signal(50.0) == 0.0
    def test_boundary_oversold(self):
        assert self._rsi_signal(10.1) == 0.0
    def test_rsi_always_in_range(self):
        signals = [self._rsi_signal(v) for v in np.linspace(0, 100, 1000)]
        assert all(s in (-1.0, 0.0, 1.0) for s in signals)

class TestMicrostructureGuard:
    def _is_tradable_time(self, t):
        from datetime import time
        return time(9, 45) <= t <= time(15, 45)
    def test_at_open_blocked(self):
        from datetime import time
        assert not self._is_tradable_time(time(9, 35))
    def test_mid_day_allowed(self):
        from datetime import time
        assert self._is_tradable_time(time(13, 0))
    def test_near_close_blocked(self):
        from datetime import time
        assert not self._is_tradable_time(time(15, 50))
    def test_exact_tradable_boundary(self):
        from datetime import time
        assert self._is_tradable_time(time(9, 45))
    def test_exact_close_boundary(self):
        from datetime import time
        assert self._is_tradable_time(time(15, 45))

class TestSignalEngine:
    def test_signal_engine_raises_not_implemented_before_phase3(self):
        from src.strategy.signals import SignalEngine
        with pytest.raises(NotImplementedError):
            SignalEngine()


