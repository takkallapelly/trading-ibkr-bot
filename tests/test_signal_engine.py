"""
tests/test_signal_engine.py
────────────────────────────
Tests for Phase 3: Signal, rules, SignalEngine, StrategyEngine.
No network required — uses synthetic bars.
"""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timezone


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_bar(
    close=150.0, atr=2.0, rsi=5.0, bb_pct=0.02,
    ema_diff=0.5, vol_ratio=1.2,
    rsi_signal=1.0, bb_signal=1.0, ema_signal=1.0,
    signal_score=None,
    volume=1_000_000,
    ticker_time=None,
) -> pd.Series:
    """Create a synthetic feature bar for testing."""
    score = signal_score if signal_score is not None else (
        rsi_signal * 0.5 + bb_signal * 0.3 + ema_signal * 0.2
    )
    ts = ticker_time or pd.Timestamp("2024-06-15 14:00:00", tz="America/New_York")
    return pd.Series({
        "close": close, "open": close * 0.999,
        "high": close * 1.002, "low": close * 0.998,
        "volume": volume,
        "atr": atr, "atr_pct": atr / close,
        "rsi_2": rsi, "bb_pct": bb_pct,
        "ema_diff": ema_diff, "ema_fast": close + ema_diff,
        "ema_slow": close, "ema_cross": 0,
        "vol_ratio": vol_ratio, "vol_ma": volume * 0.8,
        "rsi_signal": rsi_signal, "bb_signal": bb_signal,
        "ema_signal": ema_signal, "signal_score": score,
        "frac_diff": 0.01, "bb_upper": close * 1.02,
        "bb_lower": close * 0.98, "bb_mid": close,
    }, name=ts)


# ── Signal dataclass tests ────────────────────────────────────────────────────

class TestSignal:

    def test_long_signal_is_actionable(self):
        from src.strategy.signal import Signal, Direction
        s = Signal(
            ticker="TSLA", direction=Direction.LONG, score=0.80,
            timestamp=datetime.now(timezone.utc),
            close=150.0, entry_price=150.0,
            stop_price=148.0, target_price=153.0,
        )
        assert s.is_actionable

    def test_flat_signal_not_actionable(self):
        from src.strategy.signal import flat_signal
        s = flat_signal("TSLA", "test")
        assert not s.is_actionable

    def test_risk_reward_calculation(self):
        from src.strategy.signal import Signal, Direction
        s = Signal(
            ticker="NVDA", direction=Direction.LONG, score=0.80,
            timestamp=datetime.now(timezone.utc),
            close=500.0, entry_price=500.0,
            stop_price=497.0, target_price=504.5,  # 3 risk, 4.5 reward
        )
        assert s.risk_reward == pytest.approx(1.5, rel=0.01)

    def test_risk_per_share(self):
        from src.strategy.signal import Signal, Direction
        s = Signal(
            ticker="AAPL", direction=Direction.LONG, score=0.75,
            timestamp=datetime.now(timezone.utc),
            close=200.0, entry_price=200.0,
            stop_price=198.0, target_price=203.0,
        )
        assert s.risk_per_share == pytest.approx(2.0)

    def test_to_dict_has_required_keys(self):
        from src.strategy.signal import Signal, Direction
        s = Signal(
            ticker="META", direction=Direction.SHORT, score=0.70,
            timestamp=datetime.now(timezone.utc),
            close=500.0, entry_price=500.0,
            stop_price=503.0, target_price=496.0,
        )
        d = s.to_dict()
        for key in ["ticker", "direction", "score", "entry_price",
                    "stop_price", "target_price", "risk_reward", "is_actionable"]:
            assert key in d

    def test_blocked_signal_not_actionable(self):
        from src.strategy.signal import Signal, Direction
        s = Signal(
            ticker="AMD", direction=Direction.LONG, score=0.80,
            timestamp=datetime.now(timezone.utc),
            close=150.0, entry_price=150.0,
            stop_price=148.0, target_price=153.0,
            blocked_reason="test block",
        )
        assert not s.is_actionable


# ── Rules tests ───────────────────────────────────────────────────────────────

class TestRules:

    def test_score_below_threshold_blocked(self):
        from src.strategy.rules import check_min_score
        blocked, reason = check_min_score(0.40, min_score=0.60)
        assert blocked
        assert "threshold" in reason

    def test_score_above_threshold_passes(self):
        from src.strategy.rules import check_min_score
        blocked, _ = check_min_score(0.75, min_score=0.60)
        assert not blocked

    def test_low_volume_blocked(self):
        from src.strategy.rules import check_volume
        bar = make_bar(vol_ratio=0.3)
        blocked, reason = check_volume(bar, min_vol_ratio=0.5)
        assert blocked
        assert "volume" in reason

    def test_normal_volume_passes(self):
        from src.strategy.rules import check_volume
        bar = make_bar(vol_ratio=1.5)
        blocked, _ = check_volume(bar, min_vol_ratio=0.5)
        assert not blocked

    def test_low_atr_blocked(self):
        from src.strategy.rules import check_min_atr
        bar = make_bar(close=200.0, atr=0.05)  # atr_pct = 0.025% — too low
        bar["atr_pct"] = 0.05 / 200.0
        blocked, _ = check_min_atr(bar, min_atr_pct=0.005)
        assert blocked

    def test_good_rr_passes(self):
        from src.strategy.rules import check_risk_reward
        blocked, _ = check_risk_reward(150.0, 148.0, 153.0, min_rr=1.0)
        assert not blocked

    def test_bad_rr_blocked(self):
        from src.strategy.rules import check_risk_reward
        # Risk=2, Reward=1 → R:R=0.5 < 1.0
        blocked, reason = check_risk_reward(150.0, 148.0, 151.0, min_rr=1.0)
        assert blocked
        assert "R:R" in reason

    def test_direction_conflict_long_blocked(self):
        from src.strategy.rules import check_direction_consistency
        bar = make_bar(rsi_signal=-1.0, bb_signal=1.0)
        blocked, reason = check_direction_consistency(bar, "LONG")
        assert blocked
        assert "conflict" in reason

    def test_direction_consistent_passes(self):
        from src.strategy.rules import check_direction_consistency
        bar = make_bar(rsi_signal=1.0, bb_signal=1.0)
        blocked, _ = check_direction_consistency(bar, "LONG")
        assert not blocked


# ── SignalEngine tests ────────────────────────────────────────────────────────

class TestSignalEngine:

    def test_strong_long_signal_actionable(self):
        from src.strategy.signals import SignalEngine
        engine = SignalEngine()
        bar = make_bar(rsi=5.0, bb_pct=0.02, ema_diff=0.5, signal_score=0.80)
        signal = engine.evaluate("TSLA", bar)
        assert signal.direction.value == "LONG"
        assert signal.is_actionable

    def test_strong_short_signal_blocked_in_long_only(self):
        from src.strategy.signals import SignalEngine
        engine = SignalEngine()
        bar = make_bar(
            rsi=95.0, bb_pct=0.98, ema_diff=-0.5,
            rsi_signal=-1.0, bb_signal=-1.0, ema_signal=-1.0,
            signal_score=-0.80,
        )
        signal = engine.evaluate("TSLA", bar)
        assert not signal.is_actionable

    def test_weak_signal_flat(self):
        from src.strategy.signals import SignalEngine
        engine = SignalEngine()
        bar = make_bar(signal_score=0.30)
        signal = engine.evaluate("TSLA", bar)
        assert not signal.is_actionable

    def test_entry_price_equals_close(self):
        from src.strategy.signals import SignalEngine
        engine = SignalEngine()
        bar = make_bar(close=250.0, atr=2.5, signal_score=0.80)
        signal = engine.evaluate("TSLA", bar)
        assert signal.entry_price == pytest.approx(250.0)

    def test_stop_below_entry_for_long(self):
        from src.strategy.signals import SignalEngine
        engine = SignalEngine()
        bar = make_bar(close=200.0, atr=2.0, signal_score=0.80)
        signal = engine.evaluate("TSLA", bar)
        assert signal.stop_price < signal.entry_price

    def test_target_above_entry_for_long(self):
        from src.strategy.signals import SignalEngine
        engine = SignalEngine()
        bar = make_bar(close=200.0, atr=2.0, signal_score=0.80)
        signal = engine.evaluate("TSLA", bar)
        assert signal.target_price > signal.entry_price

    def test_risk_reward_at_least_1(self):
        from src.strategy.signals import SignalEngine
        engine = SignalEngine()
        bar = make_bar(close=150.0, atr=2.0, signal_score=0.80)
        signal = engine.evaluate("TSLA", bar)
        if signal.is_actionable:
            assert signal.risk_reward >= 1.0

    def test_atr_based_stop_scales_with_volatility(self):
        from src.strategy.signals import SignalEngine
        engine = SignalEngine()

        bar_low_vol  = make_bar(close=100.0, atr=1.0, signal_score=0.80)
        bar_high_vol = make_bar(close=100.0, atr=5.0, signal_score=0.80)

        sig_low  = engine.evaluate("TSLA", bar_low_vol)
        sig_high = engine.evaluate("TSLA", bar_high_vol)

        if sig_low.is_actionable and sig_high.is_actionable:
            assert sig_high.risk_per_share > sig_low.risk_per_share

    def test_scan_returns_list(self):
        from src.strategy.signals import SignalEngine
        engine = SignalEngine()
        bars = {
            "TSLA": make_bar(signal_score=0.80),
            "NVDA": make_bar(signal_score=0.30),
            "AAPL": make_bar(signal_score=-0.80,
                             rsi_signal=-1.0, bb_signal=-1.0, ema_signal=-1.0),
        }
        signals = engine.scan(bars)
        assert isinstance(signals, list)

    def test_scan_sorted_by_score(self):
        from src.strategy.signals import SignalEngine
        engine = SignalEngine()
        bars = {
            "TSLA": make_bar(signal_score=0.70),
            "NVDA": make_bar(signal_score=0.90),
        }
        signals = engine.scan(bars)
        if len(signals) >= 2:
            assert signals[0].score >= signals[1].score

    def test_invalid_bar_returns_flat(self):
        from src.strategy.signals import SignalEngine
        from src.strategy.signal import flat_signal
        engine = SignalEngine()
        bad_bar = make_bar(close=100.0, atr=0.0)  # atr=0 is invalid
        bad_bar["close"] = 0.0                     # set close to 0 after creation
        signal = engine.evaluate("TSLA", bad_bar)
        assert not signal.is_actionable


# ── MetaLabeler tests ─────────────────────────────────────────────────────────

class TestMetaLabeler:

    def test_pass_through_approves_all(self):
        """Phase 3: no model trained → all signals approved."""
        from src.strategy.meta_labeler import MetaLabeler
        from src.strategy.signal import Signal, Direction
        labeler = MetaLabeler()

        signal = Signal(
            ticker="TSLA", direction=Direction.LONG, score=0.80,
            timestamp=datetime.now(timezone.utc),
            close=150.0, entry_price=150.0,
            stop_price=148.0, target_price=153.0,
        )
        result = labeler.approve(signal)
        assert result.meta_approved

    def test_is_not_active_without_model(self):
        from src.strategy.meta_labeler import MetaLabeler
        labeler = MetaLabeler()
        assert not labeler.is_active

    def test_status_returns_dict(self):
        from src.strategy.meta_labeler import MetaLabeler
        labeler = MetaLabeler()
        status = labeler.status()
        assert "is_active" in status
        assert "phase" in status
        assert status["phase"] == 3


# ── StrategyEngine integration test ───────────────────────────────────────────

class TestStrategyEngine:

    def test_get_signals_with_bars(self):
        from src.strategy.engine import StrategyEngine
        engine = StrategyEngine(tickers=["TSLA", "NVDA"])
        bars = {
            "TSLA": make_bar(signal_score=0.80),
            "NVDA": make_bar(signal_score=0.30),
        }
        signals = engine.get_signals(bars=bars)
        assert isinstance(signals, list)
        # TSLA should fire, NVDA should not
        tickers = [s.ticker for s in signals]
        assert "TSLA" in tickers
        assert "NVDA" not in tickers

    def test_no_signals_when_all_weak(self):
        from src.strategy.engine import StrategyEngine
        engine = StrategyEngine(tickers=["TSLA"])
        bars = {"TSLA": make_bar(signal_score=0.20)}
        signals = engine.get_signals(bars=bars)
        assert signals == []
