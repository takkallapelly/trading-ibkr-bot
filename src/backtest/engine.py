"""
src/backtest/engine.py — Walk-forward backtester with purged CV.
"""
from __future__ import annotations
from datetime import date
from pathlib import Path
import pandas as pd
import numpy as np
from loguru import logger
from rich.console import Console
from rich.table import Table
from src.config import settings, cfg, TICKERS
from src.backtest.simulator import TradeSimulator
from src.backtest.metrics import summary
from src.backtest.report import generate_html_report

console = Console()

class Backtester:
    def __init__(self, tickers=None, start=None, end=None, capital=None,
                 n_splits=None, embargo_pct=None, commission=None,
                 slippage_bps=None, position_size_usd=None):
        bt = cfg.get("backtest", {})
        self.tickers     = tickers or TICKERS
        self.start       = start  or bt.get("start_date", "2020-01-01")
        self.end         = end    or bt.get("end_date", date.today().isoformat())
        self.capital     = capital or settings.TOTAL_CAPITAL
        self.n_splits    = n_splits or bt.get("n_splits", 6)
        self.embargo_pct = embargo_pct or bt.get("embargo_pct", 0.01)
        self.report_dir  = Path(bt.get("report_dir", "reports"))
        self.simulator   = TradeSimulator(
            commission=commission or bt.get("commission_per_share", 0.005),
            slippage_bps=slippage_bps or bt.get("slippage_bps", 5.0),
            position_size_usd=position_size_usd or (self.capital * 0.10),
        )
        self._all_trades: list[dict] = []
        self._results: dict | None   = None

    def run(self) -> dict:
        logger.info(f"Backtest starting | {self.start} → {self.end} | "
                    f"{len(self.tickers)} tickers | {self.n_splits}-fold walk-forward")
        from src.data.loader import DataLoader
        loader = DataLoader(tickers=self.tickers, use_cache=True)
        all_trades = []
        for ticker in self.tickers:
            logger.info(f"Backtesting {ticker}...")
            df = loader.get(ticker, start=self.start, end=self.end)
            if df.empty or len(df) < 60:
                logger.warning(f"{ticker}: insufficient data — skipping")
                continue
            trades = self._run_walk_forward(ticker, df)
            all_trades.extend(trades)
            logger.info(f"{ticker}: {len(trades)} trades completed")
        self._all_trades = all_trades
        self._results    = summary(all_trades, self.capital)
        self._print_summary()
        return self._results

    def run_simple(self) -> dict:
        logger.info(f"Simple backtest | {self.start} → {self.end}")
        from src.data.loader import DataLoader
        loader = DataLoader(tickers=self.tickers, use_cache=True)
        all_trades = []
        for ticker in self.tickers:
            df = loader.get(ticker, start=self.start, end=self.end)
            if df.empty: continue
            trades = self.simulator.run(ticker, df)
            all_trades.extend(trades)
        self._all_trades = all_trades
        self._results    = summary(all_trades, self.capital)
        self._print_summary()
        return self._results

    def to_html(self, output_path=None) -> Path:
        if self._results is None:
            raise RuntimeError("Run backtest first: backtester.run()")
        output_path = Path(output_path or (
            self.report_dir / f"backtest_{self.start}_{self.end}.html"
        ))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        generate_html_report(
            trades=self._all_trades, metrics=self._results,
            tickers=self.tickers, start=self.start, end=self.end,
            capital=self.capital, output=output_path,
        )
        logger.success(f"HTML report saved → {output_path}")
        return output_path

    @property
    def trades(self): return self._all_trades
    @property
    def results(self): return self._results

    def _run_walk_forward(self, ticker, df):
        n = len(df)
        embargo   = max(1, int(n * self.embargo_pct))
        fold_size = n // self.n_splits
        all_trades = []
        for k in range(self.n_splits):
            test_start = k * fold_size
            test_end   = min(test_start + fold_size, n)
            if test_end - test_start < 20: continue
            trades = self.simulator.run(ticker, df, test_start, test_end)
            all_trades.extend(trades)
            logger.debug(f"{ticker} fold {k+1}/{self.n_splits} | "
                         f"test {test_start}→{test_end} | {len(trades)} trades")
        return all_trades

    def _print_summary(self):
        if not self._results: return
        r = self._results
        ticker_table = Table(title="Backtest results by ticker", show_lines=True)
        ticker_table.add_column("Ticker",    style="cyan")
        ticker_table.add_column("Trades",    style="white",  justify="right")
        ticker_table.add_column("Win %",     style="green",  justify="right")
        ticker_table.add_column("P&L $",     style="yellow", justify="right")
        ticker_table.add_column("Best",      style="green",  justify="right")
        ticker_table.add_column("Worst",     style="red",    justify="right")
        ticker_table.add_column("Exp/trade", style="white",  justify="right")
        tickers_seen = {}
        for t in self._all_trades:
            tickers_seen.setdefault(t["ticker"], []).append(t)
        for ticker, trades in sorted(tickers_seen.items()):
            closed = [t for t in trades if t.get("pnl") is not None]
            if not closed: continue
            pnls = [t["pnl"] for t in closed]
            wr   = sum(1 for p in pnls if p > 0) / len(pnls)
            ticker_table.add_row(ticker, str(len(closed)), f"{wr:.1%}",
                f"${sum(pnls):,.0f}", f"${max(pnls):,.0f}",
                f"${min(pnls):,.0f}", f"${sum(pnls)/len(pnls):.2f}")
        console.print(ticker_table)
        overall = Table(title="Overall performance", show_lines=True)
        overall.add_column("Metric", style="cyan")
        overall.add_column("Value",  style="yellow", justify="right")
        for metric, value in [
            ("Total trades",       str(r["n_trades"])),
            ("Win rate",           f"{r['win_rate']:.1%}"),
            ("Profit factor",      f"{r['profit_factor']:.2f}"),
            ("Expectancy/trade",   f"${r['expectancy']:.2f}"),
            ("Total P&L",          f"${r['total_pnl']:,.0f}"),
            ("Total return",       f"{r['total_return']:.1%}"),
            ("CAGR",               f"{r['cagr']:.1%}"),
            ("Sharpe ratio",       f"{r['sharpe']:.2f}"),
            ("Sortino ratio",      f"{r['sortino']:.2f}"),
            ("Max drawdown",       f"{r['max_drawdown']:.1%}"),
            ("Calmar ratio",       f"{r['calmar']:.2f}"),
            ("Max consec. losses", str(r["max_consecutive_losses"])),
            ("Initial capital",    f"${r['initial_capital']:,.0f}"),
            ("Final equity",       f"${r['final_equity']:,.0f}"),
        ]:
            overall.add_row(metric, value)
        console.print(overall)
