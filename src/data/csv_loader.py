"""
src/data/csv_loader.py
───────────────────────
Loads 5-minute OHLCV bars from local CSV files.

Expected CSV format (Alpaca format):
  timestamp,open,high,low,close,volume,trade_count,vwap
  2021-01-04 09:30:00-05:00,129.87,129.96,128.77,129.18,6167687.0,...

Usage:
    loader = CSVLoader(data_dir="C:/TradingBot/data")
    df = loader.load("AAPL")        # returns feature-enriched DataFrame
    all_dfs = loader.load_all()     # returns dict of all tickers
"""

from __future__ import annotations

from pathlib import Path
import pandas as pd
import numpy as np
from loguru import logger


class CSVLoader:
    """
    Loads local 5-min CSV files and builds feature-enriched DataFrames.

    Args:
        data_dir : folder containing CSV files like AAPL_5min.csv
        suffix   : filename suffix (default: "_5min.csv")
    """

    def __init__(
        self,
        data_dir: str | Path,
        suffix: str = "_5min.csv",
    ):
        self.data_dir = Path(data_dir)
        self.suffix   = suffix

        if not self.data_dir.exists():
            raise FileNotFoundError(f"Data directory not found: {self.data_dir}")

        logger.info(f"CSVLoader ready | dir={self.data_dir} | suffix={suffix}")

    def available_tickers(self) -> list[str]:
        """Return list of tickers that have CSV files."""
        files = list(self.data_dir.glob(f"*{self.suffix}"))
        tickers = [f.stem.replace(self.suffix.replace(".csv",""), "") for f in files]
        # Clean up suffix from stem
        tickers = [f.stem.split("_5min")[0] for f in files]
        return sorted(tickers)

    def load(
        self,
        ticker: str,
        start: str | None = None,
        end: str | None = None,
        compute_features: bool = True,
        cfg: dict | None = None,
    ) -> pd.DataFrame:
        """
        Load and process one ticker's CSV file.

        Args:
            ticker           : e.g. "AAPL"
            start            : filter from date "YYYY-MM-DD" (optional)
            end              : filter to date "YYYY-MM-DD" (optional)
            compute_features : compute RSI, BB, EMA, ATR etc.
            cfg              : config dict for feature computation

        Returns:
            DataFrame with datetime index (UTC) and all features.
            Empty DataFrame if file not found.
        """
        filepath = self.data_dir / f"{ticker}{self.suffix}"

        if not filepath.exists():
            logger.warning(f"{ticker}: file not found at {filepath}")
            return pd.DataFrame()

        try:
            df = self._read_csv(filepath)
        except Exception as e:
            logger.error(f"{ticker}: failed to read CSV: {e}")
            return pd.DataFrame()

        if df.empty:
            logger.warning(f"{ticker}: empty after loading")
            return pd.DataFrame()

        # Apply date filters
        if start:
            df = df[df.index >= pd.Timestamp(start, tz="UTC")]
        if end:
            df = df[df.index <= pd.Timestamp(end, tz="UTC")]

        if df.empty:
            logger.warning(f"{ticker}: empty after date filter")
            return df

        # Filter to regular trading hours only (9:30 AM - 4:00 PM ET)
        df = self._filter_trading_hours(df)

        if df.empty:
            logger.warning(f"{ticker}: empty after hours filter")
            return df

        # Compute features
        if compute_features:
            from src.data.intraday import _intraday_cfg
            feature_cfg = cfg or _intraday_cfg()
            from src.data.features import build_features
            df = build_features(df, cfg=feature_cfg)

        logger.info(
            f"{ticker}: {len(df):,} bars loaded | "
            f"{df.index[0].strftime('%Y-%m-%d')} → "
            f"{df.index[-1].strftime('%Y-%m-%d')}"
        )
        return df

    def load_all(
        self,
        tickers: list[str] | None = None,
        start: str | None = None,
        end: str | None = None,
        compute_features: bool = True,
    ) -> dict[str, pd.DataFrame]:
        """
        Load all available tickers (or a subset).

        Returns:
            dict {ticker: DataFrame}
        """
        tickers = tickers or self.available_tickers()
        results = {}

        for ticker in tickers:
            df = self.load(
                ticker, start=start, end=end,
                compute_features=compute_features
            )
            if not df.empty:
                results[ticker] = df

        logger.info(
            f"CSVLoader: loaded {len(results)}/{len(tickers)} tickers"
        )
        return results

    # ── Private ───────────────────────────────────────────────────────────────

    def _read_csv(self, filepath: Path) -> pd.DataFrame:
        """Read and clean one CSV file."""
        raw = pd.read_csv(filepath)

        # Find timestamp column (could be 'timestamp', 'datetime', 'date', 'time')
        ts_col = None
        for col in ["timestamp", "datetime", "date", "time", "Datetime", "Date"]:
            if col in raw.columns:
                ts_col = col
                break

        if ts_col is None:
            # Try using the index
            raw = pd.read_csv(filepath, index_col=0)
            ts_col = raw.index.name or "index"
            raw = raw.reset_index()
            ts_col = raw.columns[0]

        # Parse timestamps — handle timezone-aware format like "2021-01-04 09:30:00-05:00"
        try:
            raw[ts_col] = pd.to_datetime(raw[ts_col], utc=True)
        except Exception:
            raw[ts_col] = pd.to_datetime(raw[ts_col]).dt.tz_localize("America/New_York").dt.tz_convert("UTC")

        # Standardise column names to lowercase
        raw.columns = [c.lower() for c in raw.columns]
        ts_col = ts_col.lower()

        # Keep only OHLCV columns
        col_map = {}
        for std in ["open", "high", "low", "close", "volume"]:
            if std in raw.columns:
                col_map[std] = std

        df = raw[[ts_col] + list(col_map.keys())].copy()
        df = df.rename(columns=col_map)
        df = df.set_index(ts_col)
        df.index.name = "datetime"

        # Clean data
        df = df.dropna()
        df = df[df["close"] > 0]
        df = df[df["volume"] > 0]
        df = df[~df.index.duplicated(keep="first")]
        df = df.sort_index()

        # Ensure correct dtypes
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df.dropna()

    def _filter_trading_hours(self, df: pd.DataFrame) -> pd.DataFrame:
        """Keep only regular trading hours: 9:30 AM - 4:00 PM ET."""
        try:
            eastern = df.index.tz_convert("America/New_York")
            mask = (
                (eastern.time >= pd.Timestamp("09:30").time()) &
                (eastern.time <= pd.Timestamp("16:00").time()) &
                (eastern.dayofweek < 5)  # Monday-Friday only
            )
            return df[mask]
        except Exception as e:
            logger.debug(f"Hours filter failed: {e} — returning all bars")
            return df
