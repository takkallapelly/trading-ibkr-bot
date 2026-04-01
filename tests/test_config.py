"""
tests/test_config.py
Tests for settings loader and config.yaml parsing.
"""

import os
import pytest
from unittest.mock import patch
from src.config import Settings, cfg, TICKERS


class TestSettings:

    def test_default_trading_mode_is_paper(self):
        s = Settings()
        assert s.TRADING_MODE == "paper"

    def test_is_live_false_by_default(self):
        s = Settings()
        assert s.is_live is False

    def test_is_live_true_when_set(self):
        s = Settings()
        s.TRADING_MODE = "live"
        assert s.is_live is True

    def test_total_capital_default(self):
        s = Settings()
        assert s.TOTAL_CAPITAL == 25000.0

    def test_validate_passes_with_valid_config(self):
        s = Settings()
        s.validate()  # should not raise

    def test_validate_raises_on_zero_capital(self):
        with patch.dict(os.environ, {"TOTAL_CAPITAL": "0"}):
            s = Settings()
            s.TOTAL_CAPITAL = 0.0
            with pytest.raises(EnvironmentError):
                s.validate()


class TestYamlConfig:

    def test_tickers_list_not_empty(self):
        assert len(TICKERS) > 0

    def test_expected_tickers_present(self):
        for ticker in ["META", "MSFT", "AAPL"]:
            assert ticker in TICKERS

    def test_rsi_period_in_cfg(self):
        assert cfg["signals"]["rsi"]["period"] == 2

    def test_signal_weights_sum_to_one(self):
        weights = (
            cfg["signals"]["rsi"]["weight"]
            + cfg["signals"]["bollinger"]["weight"]
            + cfg["signals"]["ema"]["weight"]
        )
        assert abs(weights - 1.0) < 1e-9

    def test_kelly_fraction_is_half(self):
        assert cfg["risk"]["kelly_fraction"] == 0.5

    def test_stop_loss_atr_mult_positive(self):
        assert cfg["risk"]["stop_loss_atr_mult"] > 0

    def test_take_profit_greater_than_stop(self):
        assert (
            cfg["risk"]["take_profit_atr_mult"]
            > cfg["risk"]["stop_loss_atr_mult"]
        )

    def test_avoid_microstructure_windows(self):
        assert cfg["signals"]["avoid_first_minutes"] >= 15
        assert cfg["signals"]["avoid_last_minutes"] >= 15
