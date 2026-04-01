import pytest
from datetime import datetime, timezone

def make_signal(ticker="TSLA", score=0.80, close=200.0, atr=2.0,
                entry=200.0, stop=197.0, target=206.0):
    from src.strategy.signal import Signal, Direction
    return Signal(
        ticker=ticker, direction=Direction.LONG, score=score,
        timestamp=datetime.now(timezone.utc), close=close,
        entry_price=entry, stop_price=stop, target_price=target, atr=atr,
    )

class TestKellySizer:
    def test_positive_edge_gives_positive_size(self):
        from src.risk.kelly import KellySizer
        sizer = KellySizer(capital=25000, min_trades=0)
        size = sizer.position_size_usd(win_rate=0.55, avg_win=150, avg_loss=100)
        assert size > 0

    def test_no_edge_gives_zero(self):
        from src.risk.kelly import KellySizer
        sizer = KellySizer(capital=25000, min_trades=0)
        size = sizer.position_size_usd(win_rate=0.50, avg_win=100, avg_loss=100)
        assert size == 0.0

    def test_size_capped_at_max(self):
        from src.risk.kelly import KellySizer
        sizer = KellySizer(capital=25000, max_position_usd=2500, min_trades=0)
        size = sizer.position_size_usd(win_rate=0.80, avg_win=500, avg_loss=100)
        assert size <= 2500

    def test_shares_at_least_one(self):
        from src.risk.kelly import KellySizer
        sizer = KellySizer(capital=25000)
        shares = sizer.shares_to_buy(2500, entry_price=500.0, risk_per_share=5.0)
        assert shares >= 1

class TestDailyDrawdownBreaker:
    def test_small_loss_no_halt(self):
        from src.risk.circuit_breaker import DailyDrawdownBreaker
        b = DailyDrawdownBreaker(limit_pct=0.03, capital=25000)
        halted, _ = b.check(daily_pnl=-500, today="2024-01-15")
        assert not halted

    def test_large_loss_halts(self):
        from src.risk.circuit_breaker import DailyDrawdownBreaker
        b = DailyDrawdownBreaker(limit_pct=0.03, capital=25000)
        halted, reason = b.check(daily_pnl=-800, today="2024-01-15")
        assert halted

    def test_resets_next_day(self):
        from src.risk.circuit_breaker import DailyDrawdownBreaker
        b = DailyDrawdownBreaker(limit_pct=0.03, capital=25000)
        b.check(daily_pnl=-800, today="2024-01-15")
        halted, _ = b.check(daily_pnl=-100, today="2024-01-16")
        assert not halted

class TestConsecutiveLossBreaker:
    def test_no_halt_below_limit(self):
        from src.risk.circuit_breaker import ConsecutiveLossBreaker
        b = ConsecutiveLossBreaker(max_losses=5)
        for _ in range(4):
            b.record(-100)
        halted, _ = b.check()
        assert not halted

    def test_halts_at_limit(self):
        from src.risk.circuit_breaker import ConsecutiveLossBreaker
        b = ConsecutiveLossBreaker(max_losses=5)
        for _ in range(5):
            b.record(-100)
        halted, _ = b.check()
        assert halted

    def test_win_resets_streak(self):
        from src.risk.circuit_breaker import ConsecutiveLossBreaker
        b = ConsecutiveLossBreaker(max_losses=5)
        for _ in range(4):
            b.record(-100)
        b.record(200)
        assert b.current_streak == 0

class TestCorrelationGuard:
    def test_no_positions_passes(self):
        from src.risk.circuit_breaker import CorrelationGuard
        g = CorrelationGuard()
        halted, _ = g.check("META", [])
        assert not halted

    def test_correlated_pair_blocked(self):
        from src.risk.circuit_breaker import CorrelationGuard
        g = CorrelationGuard()
        halted, _ = g.check("META", ["GOOGL"])
        assert halted

    def test_uncorrelated_passes(self):
        from src.risk.circuit_breaker import CorrelationGuard
        g = CorrelationGuard()
        halted, _ = g.check("MSFT", ["TSLA"])
        assert not halted

class TestRiskManager:
    def test_approves_valid_signal(self):
        from src.risk.manager import RiskManager
        rm = RiskManager(capital=25000)
        order = rm.approve_entry(make_signal(), daily_pnl=0)
        assert order.approved

    def test_rejects_flat_signal(self):
        from src.risk.manager import RiskManager
        from src.strategy.signal import flat_signal
        rm = RiskManager(capital=25000)
        order = rm.approve_entry(flat_signal("TSLA", "test"))
        assert not order.approved

    def test_rejects_on_daily_drawdown(self):
        from src.risk.manager import RiskManager
        rm = RiskManager(capital=25000)
        order = rm.approve_entry(make_signal(), daily_pnl=-800, today="2024-01-15")
        assert not order.approved

    def test_rejects_on_loss_streak(self):
        from src.risk.manager import RiskManager
        rm = RiskManager(capital=25000)
        for _ in range(5):
            rm.record_trade_result({"ticker":"TSLA","pnl":-100})
        order = rm.approve_entry(make_signal())
        assert not order.approved

    def test_rejects_correlated(self):
        from src.risk.manager import RiskManager
        rm = RiskManager(capital=25000)
        rm._open_positions = [{"ticker":"META","size_usd":2000}]
        order = rm.approve_entry(make_signal(ticker="GOOGL"))
        assert not order.approved

    def test_status_has_keys(self):
        from src.risk.manager import RiskManager
        rm = RiskManager(capital=25000)
        status = rm.status()
        for key in ["capital","win_rate","loss_streak","daily_halt"]:
            assert key in status
