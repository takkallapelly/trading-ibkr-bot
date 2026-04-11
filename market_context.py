"""
src/data/market_context.py
──────────────────────────
Real-time market context for pre-trade filtering.

Checks before every trade:
  1. VIX Gate        — skip longs when VIX > 50 (panic, gap risk)
  2. SPY Trend       — is the market trending or choppy?
  3. SPY vs VWAP     — where is SPX relative to daily VWAP?
  4. Regime Filter   — is the individual stock mean-reverting?

Why this matters:
  RSI(2) mean-reversion ONLY works in mean-reverting markets.
  In a strong downtrend, an "oversold" stock keeps going lower.
  The market filter prevents entering into falling knives.

Ernest Chan (Algorithmic Trading, Ch.2):
  "The most important question before trading a mean-reversion
   strategy is whether the market is currently mean-reverting."
"""

from __future__ import annotations

import time
import threading
import pandas as pd
import numpy as np
from dataclasses import dataclass
from loguru import logger


@dataclass
class MarketContext:
    """
    Current market conditions snapshot.
    Computed once per scan and passed to signal engine.
    """
    vix:              float   # current VIX level
    spy_price:        float   # SPY last price
    spy_ema20:        float   # SPY 20-bar EMA (5-min bars)
    spy_ema50:        float   # SPY 50-bar EMA (5-min bars)
    spy_vwap:         float   # SPY daily VWAP
    spy_above_vwap:   bool    # SPY trading above VWAP
    spy_trending:     bool    # True if SPY is strongly trending
    spy_trend_dir:    str     # "UP", "DOWN", "FLAT"
    spy_trend_pct:    float   # % SPY has moved in last 20 bars
    market_regime:    str     # "BULL", "BEAR", "NEUTRAL", "PANIC"
    allow_longs:      bool    # True when conditions favour longs
    allow_shorts:     bool    # True when conditions favour shorts
    position_mult:    float   # position size multiplier (0.5-1.5)
    reason:           str     # human-readable explanation

    def __str__(self) -> str:
        return (
            f"VIX={self.vix:.1f} | SPY={self.spy_price:.1f} | "
            f"Regime={self.market_regime} | "
            f"Trend={self.spy_trend_dir}({self.spy_trend_pct:+.1f}%) | "
            f"VWAP={'above' if self.spy_above_vwap else 'below'} | "
            f"Longs={'✓' if self.allow_longs else '✗'} | "
            f"pos_mult={self.position_mult:.1f}x"
        )


# Cache market context for 60 seconds
_context_cache: dict = {"context": None, "timestamp": 0}
CONTEXT_CACHE_SECS = 60


def get_market_context(ibkr_client=None) -> MarketContext:
    """
    Get current market context. Cached for 60 seconds.

    Args:
        ibkr_client: IBKRClient for real-time data (optional)

    Returns:
        MarketContext with all market conditions
    """
    global _context_cache
    now = time.time()

    if (
        _context_cache["context"] is not None
        and now - _context_cache["timestamp"] < CONTEXT_CACHE_SECS
    ):
        return _context_cache["context"]

    ctx = _build_context(ibkr_client)
    _context_cache = {"context": ctx, "timestamp": now}
    logger.info(f"Market context: {ctx}")
    return ctx


def _build_context(ibkr_client=None) -> MarketContext:
    """Build fresh market context from IBKR or yfinance."""

    # ── Fetch VIX ────────────────────────────────────────────────────────────
    try:
        from src.data.vix import get_vix
        vix = get_vix(ibkr_client)
    except Exception:
        vix = 20.0

    # ── Fetch SPY bars ────────────────────────────────────────────────────────
    spy_bars = _fetch_spy_bars(ibkr_client)

    if spy_bars.empty or len(spy_bars) < 20:
        # Not enough data — allow trading with reduced size
        return MarketContext(
            vix=vix, spy_price=0, spy_ema20=0, spy_ema50=0, spy_vwap=0,
            spy_above_vwap=True, spy_trending=False, spy_trend_dir="FLAT",
            spy_trend_pct=0, market_regime="NEUTRAL",
            allow_longs=True, allow_shorts=False,
            position_mult=0.75,
            reason="Insufficient SPY data — trading with reduced size"
        )

    close  = spy_bars["close"]
    high   = spy_bars["high"]
    low    = spy_bars["low"]
    volume = spy_bars["volume"]

    # ── Calculate indicators ──────────────────────────────────────────────────
    ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
    price = float(close.iloc[-1])

    # VWAP (daily)
    typical = (high + low + close) / 3
    vwap    = float((typical * volume).sum() / volume.sum()) if volume.sum() > 0 else price

    # Trend: % change over last 20 bars
    price_20ago   = float(close.iloc[-20]) if len(close) >= 20 else price
    trend_pct     = (price - price_20ago) / price_20ago * 100

    # Trend detection
    spy_trending  = abs(trend_pct) > 1.5  # >1.5% move in 20 bars = trending
    if trend_pct > 1.5:
        trend_dir = "UP"
    elif trend_pct < -1.5:
        trend_dir = "DOWN"
    else:
        trend_dir = "FLAT"

    spy_above_vwap = price > vwap

    # ── Market Regime ─────────────────────────────────────────────────────────
    # Combine VIX and SPY trend
    if vix > 50:
        regime       = "PANIC"
        allow_longs  = False     # too dangerous — gap risk
        allow_shorts = True      # shorts work in panic
        pos_mult     = 0.5
        reason       = f"VIX={vix:.0f} PANIC — longs blocked, gap risk too high"

    elif vix > 35:
        # High fear — mean reversion works BEST but be selective
        if trend_dir == "DOWN" and trend_pct < -3:
            regime       = "BEAR"
            allow_longs  = False   # don't buy into a crash
            allow_shorts = True
            pos_mult     = 0.75
            reason       = f"VIX={vix:.0f} HIGH + SPY down {trend_pct:.1f}% — avoid longs"
        else:
            regime       = "VOLATILE"
            allow_longs  = True    # bounces are strong in high VIX
            allow_shorts = True
            pos_mult     = 1.25    # bigger size — edge is stronger
            reason       = f"VIX={vix:.0f} HIGH — RSI(2) edge is strongest here"

    elif vix > 20:
        # Elevated fear — normal conditions for mean reversion
        if trend_dir == "DOWN" and trend_pct < -2:
            regime       = "BEAR"
            allow_longs  = True    # can still buy dips but be careful
            allow_shorts = True
            pos_mult     = 0.75
            reason       = f"VIX={vix:.0f} elevated + SPY down {trend_pct:.1f}% — reduced size"
        else:
            regime       = "NEUTRAL"
            allow_longs  = True
            allow_shorts = False
            pos_mult     = 1.0
            reason       = f"VIX={vix:.0f} normal — standard RSI(2) conditions"

    elif vix > 15:
        # Low-normal fear
        regime       = "BULL" if trend_dir == "UP" else "NEUTRAL"
        allow_longs  = True
        allow_shorts = False
        pos_mult     = 0.75  # reduced — edge is weaker in low vol
        reason       = f"VIX={vix:.0f} low — RSI(2) edge is weaker, reduced size"

    else:
        # Very low fear — mean reversion often fails
        regime       = "LOW_VOL"
        allow_longs  = True   # still trade but small
        allow_shorts = False
        pos_mult     = 0.5
        reason       = f"VIX={vix:.0f} very low — weak mean-reversion conditions"

    return MarketContext(
        vix            = vix,
        spy_price      = price,
        spy_ema20      = ema20,
        spy_ema50      = ema50,
        spy_vwap       = vwap,
        spy_above_vwap = spy_above_vwap,
        spy_trending   = spy_trending,
        spy_trend_dir  = trend_dir,
        spy_trend_pct  = round(trend_pct, 2),
        market_regime  = regime,
        allow_longs    = allow_longs,
        allow_shorts   = allow_shorts,
        position_mult  = pos_mult,
        reason         = reason,
    )


def _fetch_spy_bars(ibkr_client=None) -> pd.DataFrame:
    """Fetch SPY 5-min bars. Uses IBKR if connected, else yfinance."""

    # Try IBKR first
    if ibkr_client and getattr(ibkr_client, "is_connected", lambda: False)():
        try:
            df = _fetch_spy_ibkr(ibkr_client)
            if not df.empty:
                return df
        except Exception as e:
            logger.debug(f"SPY IBKR fetch failed: {e}")

    # Fallback to yfinance
    return _fetch_spy_yfinance()


def _fetch_spy_ibkr(client) -> pd.DataFrame:
    """Fetch SPY 5-min bars from IBKR."""
    try:
        received = threading.Event()
        bars_data = []
        req_id = client.next_order_id()

        from ibapi.contract import Contract
        contract          = Contract()
        contract.symbol   = "SPY"
        contract.secType  = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"

        original_hist    = getattr(client, "historicalData",    None)
        original_hist_end = getattr(client, "historicalDataEnd", None)

        def on_bar(reqId, bar):
            if reqId == req_id:
                bars_data.append({
                    "datetime": pd.Timestamp(bar.date),
                    "open":     bar.open,
                    "high":     bar.high,
                    "low":      bar.low,
                    "close":    bar.close,
                    "volume":   bar.volume,
                })
            if original_hist:
                original_hist(reqId, bar)

        def on_end(reqId, start, end):
            if reqId == req_id:
                received.set()
            if original_hist_end:
                original_hist_end(reqId, start, end)

        client.historicalData    = on_bar
        client.historicalDataEnd = on_end

        client.reqHistoricalData(
            req_id, contract, "", "2 D", "5 mins",
            "TRADES", 1, 1, False, []
        )
        received.wait(timeout=10)

        if original_hist:    client.historicalData    = original_hist
        if original_hist_end: client.historicalDataEnd = original_hist_end

        if bars_data:
            df = pd.DataFrame(bars_data).set_index("datetime").sort_index()
            return df

    except Exception as e:
        logger.debug(f"SPY IBKR bars error: {e}")

    return pd.DataFrame()


def _fetch_spy_yfinance() -> pd.DataFrame:
    """Fetch SPY 5-min bars from yfinance."""
    try:
        import yfinance as yf
        raw = yf.download(
            "SPY", period="2d", interval="5m",
            progress=False, threads=False
        )
        if not raw.empty:
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            raw.columns = [c.lower() for c in raw.columns]
            return raw.dropna()
    except Exception as e:
        logger.debug(f"SPY yfinance error: {e}")
    return pd.DataFrame()
