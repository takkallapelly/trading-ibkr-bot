"""
src/data/fetcher.py
───────────────────
Downloads historical OHLCV bars from Yahoo Finance via yfinance.
Returns clean pandas DataFrames with consistent column names.

Used by DataLoader — never call this directly from strategy code.
"""

import time
import pandas as pd
import yfinance as yf
from loguru import logger


# Column names we use everywhere in the project
OHLCV_COLS = ["open", "high", "low", "close", "volume"]


class YFinanceFetcher:
    """
    Fetches OHLCV data from Yahoo Finance.

    Args:
        interval : bar size — "5m", "15m", "1h", "1d"
        max_retries : retry failed downloads this many times
        retry_delay : seconds to wait between retries
    """

    # yfinance limits: 5m data only available for last 60 days
    # For backtesting we use 1d bars for history, 5m for recent
    INTRADAY_LIMIT_DAYS = 59

    def __init__(
        self,
        interval: str = "5m",
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ):
        self.interval = interval
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def fetch(
        self,
        ticker: str,
        start: str,
        end: str | None = None,
    ) -> pd.DataFrame:
        """
        Download OHLCV bars for one ticker.

        Args:
            ticker : e.g. "TSLA"
            start  : "YYYY-MM-DD"
            end    : "YYYY-MM-DD" or None (= today)

        Returns:
            DataFrame with columns [open, high, low, close, volume]
            indexed by UTC datetime. Empty DataFrame on failure.
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.debug(
                    f"Fetching {ticker} | interval={self.interval} | "
                    f"{start} → {end or 'today'} | attempt {attempt}"
                )
                raw = yf.download(
                    ticker,
                    start=start,
                    end=end,
                    interval=self.interval,
                    auto_adjust=True,   # adjusts for splits/dividends
                    progress=False,
                    threads=False,
                )

                if raw.empty:
                    logger.warning(f"{ticker}: no data returned from yfinance")
                    return pd.DataFrame()

                df = self._clean(raw, ticker)
                logger.info(
                    f"{ticker}: {len(df):,} bars | "
                    f"{df.index[0].date()} → {df.index[-1].date()}"
                )
                return df

            except Exception as e:
                logger.warning(f"{ticker} fetch attempt {attempt} failed: {e}")
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)

        logger.error(f"{ticker}: all {self.max_retries} fetch attempts failed")
        return pd.DataFrame()

    def fetch_multi(
        self,
        tickers: list[str],
        start: str,
        end: str | None = None,
    ) -> dict[str, pd.DataFrame]:
        """
        Fetch multiple tickers. Returns dict {ticker: DataFrame}.
        Skips tickers that fail — never raises.
        """
        results = {}
        for ticker in tickers:
            df = self.fetch(ticker, start=start, end=end)
            if not df.empty:
                results[ticker] = df
            # Small delay to be polite to Yahoo Finance
            time.sleep(0.3)
        logger.info(
            f"Fetched {len(results)}/{len(tickers)} tickers successfully"
        )
        return results

    # ── Private helpers ───────────────────────────────────────────────────────

    def _clean(self, raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
        """Standardise column names, drop bad rows, ensure UTC index."""
        # yfinance sometimes returns MultiIndex columns
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        # Lowercase all column names
        raw.columns = [c.lower() for c in raw.columns]

        # Keep only OHLCV columns
        available = [c for c in OHLCV_COLS if c in raw.columns]
        df = raw[available].copy()

        # Ensure datetime index is timezone-aware (UTC)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        # Drop rows with any NaN or zero close price
        df = df.dropna()
        df = df[df["close"] > 0]

        # Drop duplicate timestamps
        df = df[~df.index.duplicated(keep="first")]

        # Sort chronologically
        df = df.sort_index()

        return df
