"""Tests for trade diagnostic analyzer."""
import pytest
import pandas as pd
from src.diagnostics.trade_analyzer import TradeAnalyzer


@pytest.fixture
def sample_trades():
    """Minimal trade log with enough data to test all slices."""
    return pd.DataFrame({
        "ticker":       ["MSFT","MSFT","META","META","JPM","MSFT","META","JPM","MSFT","META"],
        "side":         ["LONG"] * 10,
        "entry_time":   [
            "2026-01-06 10:00:00","2026-01-07 14:00:00",
            "2026-01-08 10:30:00","2026-01-09 13:00:00",
            "2026-01-10 11:00:00","2026-01-13 09:50:00",
            "2026-01-14 15:30:00","2026-01-15 10:00:00",
            "2026-01-16 11:00:00","2026-01-17 13:00:00",
        ],
        "exit_time":    [
            "2026-01-06 11:00:00","2026-01-07 15:00:00",
            "2026-01-08 11:00:00","2026-01-09 14:00:00",
            "2026-01-10 12:00:00","2026-01-13 10:50:00",
            "2026-01-14 16:00:00","2026-01-15 11:00:00",
            "2026-01-16 12:00:00","2026-01-17 14:00:00",
        ],
        "pnl":          [120, -80, 200, -60, 90, -110, -40, 150, 80, -30],
        "signal_score": [0.75, 0.62, 0.85, 0.61, 0.70, 0.65, 0.63, 0.80, 0.72, 0.64],
        "exit_reason":  ["TAKE_PROFIT","STOP_LOSS","TAKE_PROFIT","STOP_LOSS",
                         "TAKE_PROFIT","STOP_LOSS","STOP_LOSS","TAKE_PROFIT",
                         "TAKE_PROFIT","STOP_LOSS"],
        "entry_price":  [400,401,500,501,200,402,502,201,403,503],
        "exit_price":   [402,399,504,499,201,400,501,203,405,502],
        "stop_price":   [398,399,497,499,199,400,500,199,401,501],
        "target_price": [404,403,506,503,203,404,505,205,406,506],
        "qty":          [10,10,5,5,15,10,5,15,10,5],
    })


def test_overall_stats(sample_trades):
    az = TradeAnalyzer(sample_trades)
    stats = az.overall_stats()
    assert stats["n_trades"] == 10
    assert 0 < stats["win_rate"] < 1
    assert "total_pnl" in stats
    assert "expectancy" in stats
    assert "profit_factor" in stats


def test_by_ticker(sample_trades):
    az = TradeAnalyzer(sample_trades)
    result = az.by_ticker()
    assert "MSFT" in result.index
    assert "META" in result.index
    assert "win_rate" in result.columns
    assert "total_pnl" in result.columns


def test_by_hour(sample_trades):
    az = TradeAnalyzer(sample_trades)
    result = az.by_hour()
    assert not result.empty
    assert "win_rate" in result.columns
    assert "n_trades" in result.columns


def test_by_day_of_week(sample_trades):
    az = TradeAnalyzer(sample_trades)
    result = az.by_day_of_week()
    assert not result.empty
    assert "win_rate" in result.columns


def test_by_score_bucket(sample_trades):
    az = TradeAnalyzer(sample_trades)
    result = az.by_score_bucket()
    assert not result.empty
    assert "win_rate" in result.columns


def test_by_exit_reason(sample_trades):
    az = TradeAnalyzer(sample_trades)
    result = az.by_exit_reason()
    assert "TAKE_PROFIT" in result.index or "STOP_LOSS" in result.index


def test_edge_leakage_report(sample_trades):
    az = TradeAnalyzer(sample_trades)
    leakage = az.edge_leakage_report()
    # Must return list of (finding, severity) tuples
    assert isinstance(leakage, list)
    for item in leakage:
        assert len(item) == 2
        assert item[1] in ("HIGH", "MEDIUM", "LOW")


def test_empty_trades_raises():
    az = TradeAnalyzer(pd.DataFrame())
    with pytest.raises(ValueError, match="No closed trades"):
        az.overall_stats()


from src.diagnostics.report import DiagnosticReport

def test_report_renders_terminal(sample_trades):
    az = TradeAnalyzer(sample_trades)
    rpt = DiagnosticReport(az)
    # Should not raise; returns None (prints to console)
    rpt.print_terminal()


def test_report_saves_html(sample_trades, tmp_path):
    az = TradeAnalyzer(sample_trades)
    rpt = DiagnosticReport(az)
    out = tmp_path / "diag.html"
    rpt.save_html(out)
    assert out.exists()
    content = out.read_text()
    assert "Win Rate" in content
    assert "Edge Leakage" in content
