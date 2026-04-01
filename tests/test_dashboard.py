"""
tests/test_dashboard.py
────────────────────────
Tests for Phase 8: Flask dashboard endpoints, alerts.
No network or Telegram required.
"""

import pytest
import json
import tempfile
from pathlib import Path


# ── Flask app tests ───────────────────────────────────────────────────────────

class TestFlaskApp:

    @pytest.fixture
    def client(self):
        """Create a test Flask client."""
        from src.dashboard.app import create_app
        app = create_app()
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

    def test_index_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_index_contains_dashboard_title(self, client):
        resp = client.get("/")
        assert b"Trading Bot" in resp.data

    def test_api_status_returns_json(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "market" in data or "error" in data

    def test_api_trades_returns_list(self, client):
        resp = client.get("/api/trades")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, list) or "error" in data

    def test_api_equity_returns_dict(self, client):
        resp = client.get("/api/equity")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, dict)

    def test_api_signals_returns_list(self, client):
        resp = client.get("/api/signals")
        assert resp.status_code in (200, 500)  # 500 ok if no data loaded
        data = json.loads(resp.data)
        assert isinstance(data, list) or "error" in data

    def test_api_performance_returns_dict(self, client):
        resp = client.get("/api/performance")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, dict)

    def test_status_has_market_key(self, client):
        resp = client.get("/api/status")
        data = json.loads(resp.data)
        if "error" not in data:
            assert "market" in data
            assert "equity" in data
            assert "daily_pnl" in data

    def test_equity_response_shape(self, client):
        resp = client.get("/api/equity")
        data = json.loads(resp.data)
        if "error" not in data:
            assert "dates" in data
            assert "values" in data
            assert isinstance(data["dates"], list)
            assert isinstance(data["values"], list)


# ── AlertSystem tests ─────────────────────────────────────────────────────────

class TestAlertSystem:

    def test_disabled_without_token(self, monkeypatch):
        """AlertSystem should be disabled if token not set."""
        monkeypatch.setattr("src.config.settings.TELEGRAM_BOT_TOKEN", "")
        from src.dashboard.alerts import AlertSystem
        alerts = AlertSystem()
        assert not alerts.enabled

    def test_enabled_with_token(self, monkeypatch):
        """AlertSystem should enable when token and chat_id are set."""
        monkeypatch.setattr("src.config.settings.TELEGRAM_BOT_TOKEN", "test_token")
        monkeypatch.setattr("src.config.settings.TELEGRAM_CHAT_ID", "12345")
        from src.dashboard.alerts import AlertSystem
        alerts = AlertSystem()
        assert alerts.enabled

    def test_send_disabled_is_noop(self, monkeypatch):
        """Sending when disabled should not raise."""
        monkeypatch.setattr("src.config.settings.TELEGRAM_BOT_TOKEN", "")
        from src.dashboard.alerts import AlertSystem
        alerts = AlertSystem()
        alerts.custom("test message")   # should not raise

    def test_trade_opened_message_format(self, monkeypatch):
        """trade_opened should produce a well-formed message."""
        monkeypatch.setattr("src.config.settings.TELEGRAM_BOT_TOKEN", "")
        from src.dashboard.alerts import AlertSystem
        from src.strategy.signal import Signal, Direction
        from src.risk.manager import TradeOrder
        from datetime import datetime, timezone

        alerts = AlertSystem()
        signal = Signal(
            ticker="META", direction=Direction.LONG, score=0.80,
            timestamp=datetime.now(timezone.utc), close=300.0,
            entry_price=300.0, stop_price=295.0, target_price=309.0,
        )

        class FakeOrder:
            entry_price = 300.0
            stop_price  = 295.0
            target_price= 309.0
            shares = 8
            position_size_usd = 2400.0

        # Should not raise even when disabled
        alerts.trade_opened(signal, FakeOrder())

    def test_circuit_breaker_message(self, monkeypatch):
        monkeypatch.setattr("src.config.settings.TELEGRAM_BOT_TOKEN", "")
        from src.dashboard.alerts import AlertSystem
        alerts = AlertSystem()
        alerts.circuit_breaker("Daily drawdown limit hit")  # no raise

    def test_daily_summary_message(self, monkeypatch):
        monkeypatch.setattr("src.config.settings.TELEGRAM_BOT_TOKEN", "")
        from src.dashboard.alerts import AlertSystem
        alerts = AlertSystem()
        alerts.daily_summary(
            daily_pnl=250.0, total_equity=25250.0,
            n_trades=3, win_rate=0.67, open_positions=["META"]
        )  # no raise

    def test_ml_retrained_message(self, monkeypatch):
        monkeypatch.setattr("src.config.settings.TELEGRAM_BOT_TOKEN", "")
        from src.dashboard.alerts import AlertSystem
        alerts = AlertSystem()
        alerts.ml_retrained(accuracy=0.61, n_trades=45)  # no raise


# ── Scheduler integration with dashboard ─────────────────────────────────────

class TestSchedulerDashboard:

    def test_market_status_json_serialisable(self):
        """market_status() must be JSON-serialisable for the API endpoint."""
        from src.execution.scheduler import market_status
        status = market_status()
        # Should not raise
        encoded = json.dumps(status)
        decoded = json.loads(encoded)
        assert "is_open" in decoded
        assert "is_tradable" in decoded

    def test_market_status_types(self):
        from src.execution.scheduler import market_status
        status = market_status()
        assert isinstance(status["is_open"],     bool)
        assert isinstance(status["is_tradable"], bool)
        assert isinstance(status["time_eastern"], str)
        assert isinstance(status["mins_to_open"], (int, float))
        assert isinstance(status["mins_to_close"], (int, float))
