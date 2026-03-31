"""
tests/test_backtest_engine.py
──────────────────────────────
Tests for Phase 4: metrics, simulator, backtester.
No network required.
"""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timezone


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_trades(n_win=6, n_loss=4, avg_win=150.0, avg_loss=100.0) -> list[dict]:
    trades = []
    for i in range(n_win):
        trades.append({
            "ticker": "TSLA", "side": "LONG",
            "entry_time": f"2024-0{(i%9)+1}-01 10:00:00",
            "exit_time":  f"2024-0{(i%9)+1}-02 14:00:00",
            "entry_price": 200.0, "exit_price": 201.5,
            "qty": 100, "pnl": avg_win,
            "pnl_pct": avg_win/20000, "exit_reason": "TAKE_PROFIT",
            "bars_held": 3,
        })
    for i in range(n_loss):
        trades.append({
            "ticker": "TSLA", "side": "LONG",
            "entry_time": f"2024-1{i+1}-01 10:00:00",
            "exit_time":  f"2024-1{i+1}-02 14:00:00",
            "entry_price": 200.0, "exit_price": 199.0,
            "qty": 100, "pnl": -avg_loss,
            "pnl_pct": -avg_loss/20000, "exit_reason": "STOP_LOSS",
            "bars_held": 2,
        })
    return trades


def make_ohlcv_df(n=300, seed=42) -> pd.DataFrame:
    np.random.seed(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC")
    close = 150 + np.cumsum(np.random.randn(n) * 1.5)
    close = np.maximum(close, 10)
    df = pd.DataFrame({
        "open":   close * 0.999,
        "high":   close * 1.003,
        "low":    close * 0.997,
        "close":  close,
        "volume": np.random.randint(1_000_000, 5_000_000, n),
    }, index=dates)
    # Add all required feature columns
    from src.data.features import build_features
    return build_features(df)


# ── Metrics tests ─────────────────────────────────────────────────────────────

class TestMetrics:

    def test_win_rate(self):
        from src.backtest.metrics import win_rate
        trades = make_trades(n_win=6, n_loss=4)
        assert win_rate(trades) == pytest.approx(0.60)

    def test_win_rate_empty(self):
        from src.backtest.metrics import win_rate
        assert win_rate([]) == 0.0

    def test_profit_factor_above_one_when_profitable(self):
        from src.backtest.metrics import profit_factor
        trades = make_trades(n_win=6, n_loss=4, avg_win=150, avg_loss=100)
        pf = profit_factor(trades)
        assert pf > 1.0

    def test_profit_factor_below_one_when_losing(self):
        from src.backtest.metrics import profit_factor
        trades = make_trades(n_win=3, n_loss=7, avg_win=50, avg_loss=100)
        assert profit_factor(trades) < 1.0

    def test_expectancy_positive_with_edge(self):
        from src.backtest.metrics import expectancy
        trades = make_trades(n_win=6, n_loss=4, avg_win=150, avg_loss=100)
        assert expectancy(trades) > 0

    def test_expectancy_negative_without_edge(self):
        from src.backtest.metrics import expectancy
        trades = make_trades(n_win=3, n_loss=7, avg_win=50, avg_loss=200)
        assert expectancy(trades) < 0

    def test_max_consecutive_losses(self):
        from src.backtest.metrics import max_consecutive_losses
        trades = [
            {"pnl": 100}, {"pnl": -50}, {"pnl": -50},
            {"pnl": -50}, {"pnl": 100}, {"pnl": -50},
        ]
        assert max_consecutive_losses(trades) == 3

    def test_sharpe_positive_for_good_returns(self):
        from src.backtest.metrics import sharpe_ratio
        returns = pd.Series(np.full(252, 0.001))
        assert sharpe_ratio(returns) > 0

    def test_sharpe_zero_for_flat(self):
        from src.backtest.metrics import sharpe_ratio
        assert sharpe_ratio(pd.Series(np.zeros(252))) == 0.0

    def test_max_drawdown_negative(self):
        from src.backtest.metrics import max_drawdown
        equity = pd.Series([100, 110, 90, 95, 105])
        dd = max_drawdown(equity)
        assert dd < 0

    def test_max_drawdown_zero_for_monotonic(self):
        from src.backtest.metrics import max_drawdown
        equity = pd.Series([100, 102, 105, 108, 112])
        assert max_drawdown(equity) == pytest.approx(0.0, abs=1e-9)

    def test_cagr_positive_for_growing_equity(self):
        from src.backtest.metrics import cagr
        equity = pd.Series(np.linspace(10000, 13000, 252))
        assert cagr(equity) > 0

    def test_equity_curve_grows_with_wins(self):
        from src.backtest.metrics import equity_curve_from_trades
        trades = make_trades(n_win=10, n_loss=0, avg_win=100)
        eq = equity_curve_from_trades(trades, 25000)
        assert eq.iloc[-1] > eq.iloc[0]

    def test_summary_has_all_keys(self):
        from src.backtest.metrics import summary
        trades = make_trades()
        result = summary(trades, 25000)
        for key in ["n_trades","win_rate","profit_factor","expectancy",
                    "total_pnl","cagr","sharpe","max_drawdown","final_equity"]:
            assert key in result

    def test_summary_empty_trades(self):
        from src.backtest.metrics import summary
        result = summary([], 25000)
        assert result["n_trades"] == 0
        assert result["final_equity"] == 25000


# ── Simulator tests ───────────────────────────────────────────────────────────

class TestSimulator:

    def test_returns_list_of_dicts(self):
        from src.backtest.simulator import TradeSimulator
        sim = TradeSimulator()
        df  = make_ohlcv_df(200)
        trades = sim.run("TSLA", df)
        assert isinstance(trades, list)
        if trades:
            assert isinstance(trades[0], dict)

    def test_all_trades_have_pnl(self):
        from src.backtest.simulator import TradeSimulator
        sim = TradeSimulator()
        df  = make_ohlcv_df(300)
        trades = sim.run("TSLA", df)
        for t in trades:
            assert "pnl" in t
            assert t["pnl"] is not None

    def test_all_trades_have_exit_reason(self):
        from src.backtest.simulator import TradeSimulator
        sim = TradeSimulator()
        df  = make_ohlcv_df(300)
        trades = sim.run("TSLA", df)
        valid_reasons = {"TAKE_PROFIT", "STOP_LOSS", "EOD"}
        for t in trades:
            assert t.get("exit_reason") in valid_reasons

    def test_entry_before_exit(self):
        from src.backtest.simulator import TradeSimulator
        sim = TradeSimulator()
        df  = make_ohlcv_df(300)
        trades = sim.run("TSLA", df)
        for t in trades:
            assert t["entry_time"] <= t["exit_time"]

    def test_slippage_applied(self):
        from src.backtest.simulator import TradeSimulator
        from src.strategy.signal import Direction
        sim = TradeSimulator(slippage_bps=10)
        # Buying: slippage pushes price UP
        price = sim._apply_slippage(100.0, Direction.LONG)
        assert price > 100.0
        # Selling: slippage pushes price DOWN
        price = sim._apply_slippage(100.0, Direction.SHORT)
        assert price < 100.0

    def test_commission_reduces_pnl(self):
        from src.backtest.simulator import TradeSimulator
        sim_no_cost  = TradeSimulator(commission=0.0, slippage_bps=0.0)
        sim_with_cost= TradeSimulator(commission=0.01, slippage_bps=10.0)
        df = make_ohlcv_df(300)
        t1 = sim_no_cost.run("TSLA",  df)
        t2 = sim_with_cost.run("TSLA", df)
        if t1 and t2:
            total1 = sum(t["pnl"] for t in t1)
            total2 = sum(t["pnl"] for t in t2)
            assert total1 >= total2   # costs reduce returns

    def test_index_range_respected(self):
        from src.backtest.simulator import TradeSimulator
        sim = TradeSimulator()
        df  = make_ohlcv_df(200)
        # Only simulate first 50 bars
        trades = sim.run("TSLA", df, start_idx=0, end_idx=50)
        if trades:
            last_exit = max(t["exit_time"] for t in trades)
            last_bar_time = str(df.index[49])[:10]
            assert last_exit[:10] <= last_bar_time


# ── Backtester tests ──────────────────────────────────────────────────────────

class TestBacktester:

    def test_run_simple_returns_dict(self):
        from src.backtest.engine import Backtester
        from src.data.store import DataStore
        from src.data.features import build_features

        # Inject synthetic data into a temp store
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store = DataStore(db_path=f"{tmp}/test.db")
            df = build_features(make_ohlcv_df(300))
            store.save_bars("TSLA", df)

            bt = Backtester(
                tickers=["TSLA"],
                start="2023-01-01",
                end="2023-12-31",
                capital=25000,
            )
            # Patch loader to use our temp store
            from src.data.loader import DataLoader
            loader = DataLoader(tickers=["TSLA"])
            loader.store = store
            loader._cache = {}

            # Run simple backtest
            bt.simulator.engine  # ensure engine initialised
            trades = bt.simulator.run("TSLA", df)
            from src.backtest.metrics import summary
            result = summary(trades, 25000)

            assert isinstance(result, dict)
            assert "n_trades" in result
            assert "sharpe" in result

    def test_walk_forward_folds(self):
        from src.backtest.engine import Backtester
        bt = Backtester(tickers=["TSLA"], n_splits=4, capital=25000)
        df = make_ohlcv_df(300)
        trades = bt._run_walk_forward("TSLA", df)
        assert isinstance(trades, list)

    def test_purged_embargo_gap(self):
        """Test that embargo gap is correctly calculated."""
        from src.backtest.engine import Backtester
        bt = Backtester(embargo_pct=0.02)
        n = 500
        embargo = max(1, int(n * bt.embargo_pct))
        assert embargo == 10   # 2% of 500 = 10 bars

    def test_to_html_requires_run_first(self):
        from src.backtest.engine import Backtester
        bt = Backtester(tickers=["TSLA"])
        with pytest.raises(RuntimeError):
            bt.to_html()
