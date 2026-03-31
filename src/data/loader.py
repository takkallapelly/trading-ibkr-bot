"""
src/data/loader.py
──────────────────
DataLoader — the single entry point for all data operations.
Orchestrates: fetching → feature engineering → storing → loading.
"""

from __future__ import annotations

from datetime import datetime, timedelta, date
from pathlib import Path

import pandas as pd
from loguru import logger
from rich.console import Console
from rich.table import Table
from rich.progress import track

from src.config import settings, cfg, TICKERS
from src.data.fetcher import YFinanceFetcher
from src.data.features import build_features
from src.data.store import DataStore


console = Console()


class DataLoader:
    """
    Orchestrates data fetching, feature engineering, and storage.

    Args:
        tickers   : list of ticker symbols. Defaults to config universe.
        interval  : bar size for intraday data ("5m", "15m", "1h", "1d")
        use_cache : if True, loads from DB instead of re-fetching
    """

    def __init__(
        self,
        tickers: list[str] | None = None,
        interval: str = "1d",
        use_cache: bool = True,
    ):
        self.tickers  = tickers or TICKERS
        self.interval = interval
        self.use_cache = use_cache
        self.store    = DataStore()
        self.fetcher  = YFinanceFetcher(interval=interval)
        self._cache: dict[str, pd.DataFrame] = {}

    def fetch_all(
        self,
        start: str | None = None,
        end: str | None = None,
        force: bool = False,
    ) -> dict[str, pd.DataFrame]:
        start = start or (date.today() - timedelta(days=5 * 365)).isoformat()
        end   = end   or date.today().isoformat()

        logger.info(
            f"DataLoader.fetch_all | {len(self.tickers)} tickers | "
            f"{start} → {end} | interval={self.interval}"
        )

        results = {}
        for ticker in track(self.tickers, description="Fetching market data..."):
            if not force and self.use_cache:
                count = self.store.bar_count(ticker)
                if count > 100:
                    logger.info(f"{ticker}: {count:,} bars in DB — skipping download")
                    df = self.get(ticker, start=start, end=end)
                    if not df.empty:
                        results[ticker] = df
                        continue

            df = self._fetch_and_process(ticker, start=start, end=end)
            if not df.empty:
                results[ticker] = df

        self._print_summary(results)
        return results

    def get(
        self,
        ticker: str,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        cache_key = f"{ticker}_{start}_{end}"
        if self.use_cache and cache_key in self._cache:
            return self._cache[cache_key]

        df = self.store.load_bars(ticker, start=start, end=end)

        if df.empty:
            logger.warning(f"{ticker}: not in database. Run loader.fetch_all() first.")
            return df

        if self.use_cache:
            self._cache[cache_key] = df

        return df

    def get_latest(self, ticker: str) -> pd.Series | None:
        df = self.get(ticker)
        if df.empty:
            return None
        return df.iloc[-1]

    def get_latest_signal(self, ticker: str) -> dict:
        bar = self.get_latest(ticker)
        if bar is None:
            return {"ticker": ticker, "signal_score": 0.0, "error": "no data"}

        return {
            "ticker":       ticker,
            "timestamp":    bar.name,
            "close":        round(bar.get("close", 0), 2),
            "signal_score": round(bar.get("signal_score", 0), 3),
            "rsi_2":        round(bar.get("rsi_2", 50), 1),
            "bb_pct":       round(bar.get("bb_pct", 0.5), 3),
            "ema_diff":     round(bar.get("ema_diff", 0), 4),
            "atr":          round(bar.get("atr", 0), 4),
            "vol_ratio":    round(bar.get("vol_ratio", 1), 2),
        }

    def refresh(self, ticker: str, days: int = 5) -> pd.DataFrame:
        start = (date.today() - timedelta(days=days)).isoformat()
        df = self._fetch_and_process(ticker, start=start)
        self._cache = {k: v for k, v in self._cache.items()
                       if not k.startswith(ticker)}
        return df

    def refresh_all(self, days: int = 5) -> None:
        logger.info(f"Refreshing all tickers | last {days} days")
        for ticker in self.tickers:
            self.refresh(ticker, days=days)

    def _fetch_and_process(
        self,
        ticker: str,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        raw = self.fetcher.fetch(ticker, start=start or "2019-01-01", end=end)
        if raw.empty:
            return pd.DataFrame()

        df = build_features(raw, cfg=cfg)
        if df.empty:
            logger.warning(f"{ticker}: no rows after feature calculation")
            return pd.DataFrame()

        self.store.save_bars(ticker, df)
        return df

    def _print_summary(self, results: dict[str, pd.DataFrame]) -> None:
        table = Table(title="Data pipeline summary", show_lines=True)
        table.add_column("Ticker",  style="cyan",    no_wrap=True)
        table.add_column("Bars",    style="green",   justify="right")
        table.add_column("From",    style="white")
        table.add_column("To",      style="white")
        table.add_column("Signal",  style="yellow",  justify="right")
        table.add_column("RSI(2)",  style="magenta", justify="right")

        for ticker, df in sorted(results.items()):
            if df.empty:
                continue
            latest = df.iloc[-1]
            table.add_row(
                ticker,
                f"{len(df):,}",
                str(df.index[0].date()),
                str(df.index[-1].date()),
                f"{latest.get('signal_score', 0):.2f}",
                f"{latest.get('rsi_2', 0):.1f}",
            )

        console.print(table)
