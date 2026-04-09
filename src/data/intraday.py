"""
src/data/intraday.py
─────────────────────
Intraday 5-minute bar fetcher.

Two sources:
  1. IBKR TWS (primary) — real-time, unlimited history via reqHistoricalData
  2. yfinance (fallback) — free, last 60 days only

For paper trading: yfinance is fine.
For live trading:  IBKR gives cleaner data with exact timestamps.

Key difference from daily bars:
  - RSI(2) thresholds need to be WIDER on 5-min bars
    Daily: oversold <10, overbought >90
    5-min: oversold <15, overbought >85  (more noise on short bars)
  - ATR is much smaller (cents not dollars)
  - Need more bars to warm up indicators (same bar count, shorter time)
  - Volume patterns are very different (high at open/close)
"""

from __future__ import annotations

import time
import threading
from datetime import datetime, timedelta, date
from pathlib import Path

import pandas as pd
import numpy as np
from loguru import logger

from src.config import settings, cfg, TICKERS


class IntradayFetcher:
    """
    Fetches and maintains a rolling window of 5-minute bars.

    Keeps the last N bars in memory (no database for intraday —
    too much churn). Refreshes every 5 minutes during market hours.

    Usage:
        fetcher = IntradayFetcher()
        fetcher.warmup()                    # load initial history
        bars = fetcher.get_latest("META")   # get latest feature bar
        all_bars = fetcher.get_all("META")  # get full DataFrame
    """

    # How many 5-min bars to keep (78 bars = 1 full trading day)
    MAX_BARS = 78 * 5   # 5 days of 5-min bars

    def __init__(
        self,
        tickers: list[str] | None = None,
        use_ibkr: bool = False,   # True = use IBKR, False = use yfinance
        ibkr_client=None,         # IBKRClient instance if use_ibkr=True
    ):
        self.tickers    = tickers or TICKERS
        self.use_ibkr   = use_ibkr and ibkr_client is not None
        self.client     = ibkr_client
        self._bars: dict[str, pd.DataFrame] = {}
        self._lock = threading.Lock()

    def warmup(self, days: int = 5) -> None:
        """
        Load initial history for all tickers.
        Call once at bot startup before the main loop.
        """
        logger.info(
            f"IntradayFetcher warming up | {len(self.tickers)} tickers | "
            f"source={'IBKR' if self.use_ibkr else 'yfinance'} | {days} days"
        )

        for ticker in self.tickers:
            df = self._fetch(ticker, days=days)
            if not df.empty:
                with self._lock:
                    self._bars[ticker] = df
                logger.info(
                    f"{ticker}: {len(df)} 5-min bars loaded | "
                    f"{df.index[0].strftime('%Y-%m-%d %H:%M')} → "
                    f"{df.index[-1].strftime('%Y-%m-%d %H:%M')}"
                )
            else:
                logger.warning(f"{ticker}: no intraday data loaded")
            time.sleep(0.5)  # be polite to data source

    def refresh(self, ticker: str, days: int = 2) -> pd.DataFrame:
        """
        Fetch latest bars and append to existing data.
        Call every 5 minutes during market hours.
        """
        new_bars = self._fetch(ticker, days=days)
        if new_bars.empty:
            return self.get_all(ticker)

        with self._lock:
            existing = self._bars.get(ticker, pd.DataFrame())
            if existing.empty:
                combined = new_bars
            else:
                # Merge, drop duplicates, keep last MAX_BARS
                combined = pd.concat([existing, new_bars])
                combined = combined[~combined.index.duplicated(keep="last")]
                combined = combined.sort_index().tail(self.MAX_BARS)
            self._bars[ticker] = combined

        return combined

    def refresh_all(self, days: int = 2) -> None:
        """Refresh all tickers. Call every 5 minutes."""
        for ticker in self.tickers:
            self.refresh(ticker, days=days)
            time.sleep(0.3)

    def get_all(self, ticker: str) -> pd.DataFrame:
        """Return full 5-min DataFrame for a ticker."""
        with self._lock:
            return self._bars.get(ticker, pd.DataFrame()).copy()

    def get_latest(self, ticker: str) -> pd.Series | None:
        """Return the most recent 5-min bar with all features."""
        df = self.get_all(ticker)
        if df.empty:
            return None
        return df.iloc[-1]

    def get_latest_all(self) -> dict[str, pd.Series]:
        """Return latest bar for every ticker."""
        return {
            t: self.get_latest(t)
            for t in self.tickers
            if self.get_latest(t) is not None
        }

    def bar_count(self, ticker: str) -> int:
        """How many bars loaded for this ticker."""
        df = self.get_all(ticker)
        return len(df)

    def is_ready(self, min_bars: int = 30) -> bool:
        """True if all tickers have enough bars to compute indicators."""
        return all(self.bar_count(t) >= min_bars for t in self.tickers)

    # ── Private: data fetching ─────────────────────────────────────────────────

    def _fetch(self, ticker: str, days: int = 5) -> pd.DataFrame:
        """Fetch 5-min bars from the configured source."""
        if self.use_ibkr and self.client:
            df = self._fetch_from_ibkr(ticker, days)
        else:
            df = self._fetch_from_yfinance(ticker, days)

        if df.empty:
            return df

        # Compute features on intraday bars
        from src.data.features import build_features
        df = build_features(df, cfg=_intraday_cfg())

        return df

    def _fetch_from_yfinance(self, ticker: str, days: int = 5) -> pd.DataFrame:
        """
        Fetch 5-min bars from yfinance.
        Limited to last 60 days — good enough for paper trading.
        """
        import yfinance as yf

        # yfinance requires start/end for intraday
        end   = datetime.now()
        start = end - timedelta(days=min(days, 59))  # yfinance limit

        try:
            raw = yf.download(
                ticker,
                start=start.strftime("%Y-%m-%d"),
                end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),  # +1: yfinance end is exclusive
                interval="5m",
                auto_adjust=True,
                progress=False,
                threads=False,
            )
        except Exception as e:
            logger.warning(f"{ticker}: yfinance 5m fetch failed: {e}")
            return pd.DataFrame()

        if raw.empty:
            return pd.DataFrame()

        # Clean up MultiIndex columns
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        raw.columns = [c.lower() for c in raw.columns]

        # Ensure UTC timezone
        if raw.index.tz is None:
            raw.index = raw.index.tz_localize("UTC")
        else:
            raw.index = raw.index.tz_convert("UTC")

        raw = raw.dropna()
        raw = raw[raw["close"] > 0]
        raw = raw[~raw.index.duplicated(keep="first")]

        return raw

    def _fetch_from_ibkr(self, ticker: str, days: int = 5) -> pd.DataFrame:
        """
        Fetch 5-min historical bars from IBKR TWS.
        Only available when connected to TWS.
        """
        if not self.client or not self.client.is_connected():
            logger.warning(f"{ticker}: IBKR not connected — falling back to yfinance")
            return self._fetch_from_yfinance(ticker, days)

        try:
            from ibapi.contract import Contract
            contract = Contract()
            contract.symbol   = ticker
            contract.secType  = "STK"
            contract.exchange = "SMART"
            contract.currency = "USD"

            # Use a threading event to wait for the response
            received = threading.Event()
            bars_data = []

            req_id = self.client.next_order_id()

            def historical_data(reqId, bar):
                if reqId == req_id:
                    bars_data.append({
                        "datetime": pd.Timestamp(bar.date, tz="UTC"),
                        "open":     bar.open,
                        "high":     bar.high,
                        "low":      bar.low,
                        "close":    bar.close,
                        "volume":   bar.volume,
                    })

            def historical_data_end(reqId, start, end):
                if reqId == req_id:
                    received.set()

            # Monkey-patch callbacks temporarily
            self.client.historicalData    = historical_data
            self.client.historicalDataEnd = historical_data_end

            duration = f"{days} D"
            self.client.reqHistoricalData(
                req_id, contract,
                "",           # endDateTime (empty = now)
                duration,     # durationStr
                "5 mins",     # barSizeSetting
                "TRADES",     # whatToShow
                1,            # useRTH (regular trading hours only)
                1,            # formatDate
                False,        # keepUpToDate
                [],           # chartOptions
            )

            # Wait up to 15 seconds for response
            if received.wait(timeout=15):
                df = pd.DataFrame(bars_data)
                if not df.empty:
                    df = df.set_index("datetime").sort_index()
                    return df
            else:
                logger.warning(f"{ticker}: IBKR historical data timed out")

        except Exception as e:
            logger.warning(f"{ticker}: IBKR fetch error: {e}")

        return self._fetch_from_yfinance(ticker, days)


def _intraday_cfg() -> dict:
    """
    Config override for intraday (5-min) bar signal computation.

    Key differences from daily config:
    - RSI thresholds wider (more noise on short bars)
    - Same BB and EMA periods (they self-adjust)
    - ATR period shorter (14 bars = 70 min on 5-min bars)
    """
    base = dict(cfg)

    # Override signal thresholds for intraday
    intraday_signals = {
        "rsi": {
            "period":      2,
            "oversold":    25,    # wider than daily (10) — more noise
            "overbought":  75,    # wider than daily (90) — more noise
            "weight":      0.50,
        },
        "bollinger": {
            "period":  20,
            "std_dev": 2.0,
            "weight":  0.30,
        },
        "ema": {
            "fast":   9,
            "slow":   20,
            "weight": 0.20,
        },
        "min_signal_score":    0.50,
        "avoid_first_minutes": 15,
        "avoid_last_minutes":  15,
        "long_only":           True,
    }

    intraday_risk = {
        **cfg.get("risk", {}),
        "atr_period":          14,
        "stop_loss_atr_mult":  1.5,
        "take_profit_atr_mult":3.0,
        "min_atr_pct":         0.0005,  # 0.1% — realistic for 5-min bars
    }

    intraday_data = {
        **cfg.get("data", {}),
        "fractional_diff_d": 0.4,
    }

    return {
        **base,
        "signals": intraday_signals,
        "risk":    intraday_risk,
        "data":    intraday_data,
    }
