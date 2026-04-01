"""
tests/test_live_execution.py
─────────────────────────────
Tests for Phase 7: order state machine, scheduler, IBKRClient stubs.
No IBKR connection required — tests the logic, not the wire.
"""

import pytest
from datetime import datetime, timezone


# ── OrderState machine tests ──────────────────────────────────────────────────

class TestManagedOrder:

    def _make_order(self, **kwargs):
        from src.execution.order_manager import ManagedOrder, OrderState
        defaults = dict(
            ticker="META", side="LONG", signal_score=0.80,
            qty=10, entry_price=300.0, stop_price=295.0, target_price=309.0,
        )
        defaults.update(kwargs)
        return ManagedOrder(**defaults)

    def test_initial_state_is_pending(self):
        from src.execution.order_manager import OrderState
        order = self._make_order()
        assert order.state == OrderState.PENDING

    def test_valid_transition_pending_to_submitted(self):
        from src.execution.order_manager import OrderState
        order = self._make_order()
        order.transition(OrderState.SUBMITTED)
        assert order.state == OrderState.SUBMITTED

    def test_valid_transition_submitted_to_filled(self):
        from src.execution.order_manager import OrderState
        order = self._make_order()
        order.transition(OrderState.SUBMITTED)
        order.transition(OrderState.FILLED, fill_price=300.50)
        assert order.state == OrderState.FILLED
        assert order.fill_price == 300.50

    def test_invalid_transition_ignored(self):
        from src.execution.order_manager import OrderState
        order = self._make_order()
        order.transition(OrderState.CLOSED)   # invalid from PENDING
        assert order.state == OrderState.PENDING  # unchanged

    def test_closed_state_is_terminal(self):
        from src.execution.order_manager import OrderState
        order = self._make_order()
        order.transition(OrderState.SUBMITTED)
        order.transition(OrderState.FILLED, fill_price=300.0)
        order.transition(OrderState.CLOSED,
                         exit_price=309.0, exit_reason="TAKE_PROFIT", pnl=90.0)
        order.transition(OrderState.PENDING)  # invalid
        assert order.state == OrderState.CLOSED

    def test_slippage_long_positive_when_better(self):
        from src.execution.order_manager import OrderState
        order = self._make_order(entry_price=300.0)
        order.transition(OrderState.SUBMITTED)
        order.transition(OrderState.FILLED, fill_price=299.50)  # filled lower
        assert order.slippage > 0  # better than expected for LONG

    def test_slippage_long_negative_when_worse(self):
        from src.execution.order_manager import OrderState
        order = self._make_order(entry_price=300.0)
        order.transition(OrderState.SUBMITTED)
        order.transition(OrderState.FILLED, fill_price=300.80)  # filled higher
        assert order.slippage < 0  # worse than expected for LONG

    def test_is_open_when_submitted(self):
        from src.execution.order_manager import OrderState
        order = self._make_order()
        order.transition(OrderState.SUBMITTED)
        assert order.is_open

    def test_is_open_when_filled(self):
        from src.execution.order_manager import OrderState
        order = self._make_order()
        order.transition(OrderState.SUBMITTED)
        order.transition(OrderState.FILLED, fill_price=300.0)
        assert order.is_open

    def test_not_open_when_closed(self):
        from src.execution.order_manager import OrderState
        order = self._make_order()
        order.transition(OrderState.SUBMITTED)
        order.transition(OrderState.FILLED, fill_price=300.0)
        order.transition(OrderState.CLOSED,
                         exit_price=295.0, exit_reason="STOP_LOSS", pnl=-50.0)
        assert not order.is_open

    def test_to_trade_dict_has_required_keys(self):
        from src.execution.order_manager import OrderState
        order = self._make_order()
        order.transition(OrderState.SUBMITTED)
        order.transition(OrderState.FILLED, fill_price=300.0,
                         fill_time="2024-06-15 10:30:00")
        order.transition(OrderState.CLOSED,
                         exit_price=309.0, exit_reason="TAKE_PROFIT", pnl=90.0,
                         exit_time="2024-06-16 14:00:00")
        d = order.to_trade_dict()
        for key in ["ticker","side","entry_price","exit_price","pnl","exit_reason"]:
            assert key in d


# ── OrderManager tests ────────────────────────────────────────────────────────

class TestOrderManager:

    def _make_manager(self):
        from src.execution.order_manager import OrderManager
        return OrderManager()

    def _make_filled_order(self, ticker="META"):
        from src.execution.order_manager import ManagedOrder, OrderState
        o = ManagedOrder(ticker=ticker, side="LONG", signal_score=0.80,
                         qty=10, entry_price=300.0,
                         stop_price=295.0, target_price=309.0)
        o.parent_id = 1001
        o.stop_id   = 1002
        o.target_id = 1003
        o.transition(OrderState.SUBMITTED)
        o.transition(OrderState.FILLED, fill_price=300.0)
        return o

    def test_register_and_get_by_id(self):
        mgr   = self._make_manager()
        order = self._make_filled_order("META")
        mgr.register(order)
        assert mgr.get_by_id(1001) is order

    def test_get_by_ticker(self):
        mgr   = self._make_manager()
        order = self._make_filled_order("META")
        mgr.register(order)
        assert mgr.get_by_ticker("META") is order

    def test_has_open_position_true(self):
        mgr   = self._make_manager()
        order = self._make_filled_order("META")
        mgr.register(order)
        assert mgr.has_open_position("META")

    def test_has_open_position_false_for_unknown(self):
        mgr = self._make_manager()
        assert not mgr.has_open_position("TSLA")

    def test_open_tickers(self):
        mgr = self._make_manager()
        o1 = self._make_filled_order("META")
        o1.parent_id = 1001
        o2 = self._make_filled_order("MSFT")
        o2.parent_id = 2001  # different ID so they don't overwrite
        o2.stop_id   = 2002
        o2.target_id = 2003
        mgr.register(o1)
        mgr.register(o2)
        assert set(mgr.open_tickers()) == {"META", "MSFT"}


    def test_on_fill_advances_state(self):
        from src.execution.order_manager import OrderState
        mgr   = self._make_manager()
        order = self._make_filled_order("META")
        # Reset to submitted for testing on_fill
        order.state = OrderState.SUBMITTED
        order.fill_price = 0.0
        mgr.register(order)
        result = mgr.on_fill(1001, 300.50)
        assert result is not None
        assert result.state == OrderState.FILLED
        assert result.fill_price == 300.50

    def test_on_fill_target_closes_order(self):
        from src.execution.order_manager import OrderState
        mgr   = self._make_manager()
        order = self._make_filled_order("META")
        mgr.register(order)
        result = mgr.on_fill(1003, 309.0)  # target_id fill
        assert result.state == OrderState.CLOSED
        assert result.exit_reason == "TAKE_PROFIT"
        assert result.pnl > 0

    def test_on_fill_stop_closes_order_with_loss(self):
        from src.execution.order_manager import OrderState
        mgr   = self._make_manager()
        order = self._make_filled_order("META")
        mgr.register(order)
        result = mgr.on_fill(1002, 295.0)  # stop_id fill
        assert result.state == OrderState.CLOSED
        assert result.exit_reason == "STOP_LOSS"
        assert result.pnl < 0

    def test_execution_stats(self):
        from src.execution.order_manager import OrderState
        mgr = self._make_manager()
        for i, ticker in enumerate(["META","MSFT","AAPL"]):
            o = self._make_filled_order(ticker)
            o.parent_id = 1000 + i*3
            o.stop_id   = 1001 + i*3
            o.target_id = 1002 + i*3
            mgr.register(o)
            # Close META as win, MSFT as win, AAPL as loss
            pnl = 90.0 if ticker != "AAPL" else -50.0
            reason = "TAKE_PROFIT" if pnl > 0 else "STOP_LOSS"
            exit_p = 309.0 if pnl > 0 else 295.0
            o.transition(OrderState.CLOSED, exit_price=exit_p,
                         exit_reason=reason, pnl=pnl,
                         exit_time="2024-06-16 14:00:00")
        stats = mgr.execution_stats()
        assert stats["n_closed"] == 3
        assert stats["win_rate"] == pytest.approx(2/3, rel=0.01)


# ── Scheduler tests ───────────────────────────────────────────────────────────

class TestScheduler:

    def test_market_status_returns_dict(self):
        from src.execution.scheduler import market_status
        status = market_status()
        for key in ["is_open","is_tradable","time_eastern","weekday"]:
            assert key in status

    def test_minutes_to_open_not_negative_when_closed(self):
        from src.execution.scheduler import minutes_to_open, is_market_open
        if not is_market_open():
            mins = minutes_to_open()
            assert mins >= -1.0  # -1 means already open

    def test_is_market_open_returns_bool(self):
        from src.execution.scheduler import is_market_open
        assert isinstance(is_market_open(), bool)

    def test_is_tradable_time_returns_bool(self):
        from src.execution.scheduler import is_tradable_time
        assert isinstance(is_tradable_time(), bool)

    def test_is_sunday_returns_bool(self):
        from src.execution.scheduler import is_sunday
        assert isinstance(is_sunday(), bool)


# ── IBKRClient stub tests (no TWS needed) ─────────────────────────────────────

class TestIBKRClientStub:

    def test_import_without_ibapi(self):
        """IBKRClient should import cleanly even without ibapi installed."""
        from src.execution.ibkr_client import IBKRClient, IBKR_AVAILABLE
        assert isinstance(IBKR_AVAILABLE, bool)

    def test_not_connected_without_tws(self):
        from src.execution.ibkr_client import IBKRClient
        client = IBKRClient()
        assert not client.is_connected()

    def test_callback_registration(self):
        from src.execution.ibkr_client import IBKRClient
        client   = IBKRClient()
        received = []
        client.on("fill", lambda **kw: received.append(kw))
        client._fire_callback("fill", ticker="META", price=300.0)
        assert len(received) == 1
        assert received[0]["ticker"] == "META"

    def test_next_order_id_increments(self):
        from src.execution.ibkr_client import IBKRClient
        client = IBKRClient()
        client._next_order_id = 100
        assert client.next_order_id() == 100
        assert client.next_order_id() == 101
        assert client.next_order_id() == 102

    def test_error_10349_logged_not_raised(self):
        """Error 10349 should log a warning but not crash."""
        from src.execution.ibkr_client import IBKRClient
        client = IBKRClient()
        client.error(reqId=5, errorCode=10349,
                     errorString="Order rejected - OCA group")
        # Should not raise

    def test_informational_errors_ignored(self):
        from src.execution.ibkr_client import IBKRClient
        client = IBKRClient()
        for code in [2104, 2106, 2158]:
            client.error(reqId=0, errorCode=code,
                         errorString="Market data farm connected")
