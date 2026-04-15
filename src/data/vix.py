"""
src/data/vix.py
───────────────
VIX fetching with IBKR as primary source, yfinance as fallback.

Priority:
  1. IBKR real-time (VIX index contract on CBOE) — most reliable
  2. yfinance (^VIX) — fallback if IBKR not connected
  3. Stale cache — last known value if both sources fail
  4. Default 20.0 — if no cache exists

IBKR VIX contract:
  symbol   = "VIX"
  secType  = "IND"   (Index — NOT STK)
  exchange = "CBOE"
  currency = "USD"
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

from loguru import logger

# ── Module-level cache ────────────────────────────────────────────────────────
_vix_cache: float | None = None
_vix_cache_time: datetime | None = None
_CACHE_TTL_SECS = 120          # cache valid for 2 minutes
_DEFAULT_VIX = 20.0

# ── IBKR client reference (set by LiveTrader after connection) ────────────────
_ibkr_client = None


def set_ibkr_client(client) -> None:
    """Called by LiveTrader after IBKR connects."""
    global _ibkr_client
    _ibkr_client = client
    logger.debug("VIX: IBKR client registered")


def _get_vix_from_ibkr() -> float | None:
    """
    Fetch VIX from IBKR using the IND contract on CBOE.
    Returns float or None if not available.
    """
    if _ibkr_client is None or not _ibkr_client.is_connected():
        return None

    try:
        # Build VIX index contract
        try:
            from ibapi.contract import Contract
        except ImportError:
            return None

        contract = Contract()
        contract.symbol   = "VIX"
        contract.secType  = "IND"
        contract.exchange = "CBOE"
        contract.currency = "USD"

        # Use a threading Event to wait for callback
        result_event = threading.Event()
        result_holder = {"price": None}

        req_id = _ibkr_client.next_order_id()

        def _on_tick(ticker, field, price, **_):
            # field 4 = last price, field 9 = close
            if ticker == req_id and field in (4, 9) and price > 0:
                result_holder["price"] = price
                result_event.set()

        # Register temporary callback
        _ibkr_client.on("tick", _on_tick)

        # Request market data
        _ibkr_client.reqMktData(req_id, contract, "", True, False, [])

        # Wait up to 3 seconds
        result_event.wait(timeout=3.0)

        # Cancel market data subscription
        try:
            _ibkr_client.cancelMktData(req_id)
        except Exception:
            pass

        price = result_holder["price"]
        if price and 5.0 < price < 100.0:
            return float(price)

    except Exception as e:
        logger.debug(f"VIX IBKR fetch error: {e}")

    return None


def _get_vix_from_yfinance() -> float | None:
    """Fetch VIX from yfinance. Returns float or None."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("^VIX")
        hist = ticker.history(period="1d", interval="1m")
        if hist is not None and not hist.empty:
            price = float(hist["Close"].iloc[-1])
            if 5.0 < price < 100.0:
                return price
    except Exception as e:
        logger.debug(f"VIX yfinance fetch error: {e}")
    return None


def get_vix() -> float:
    """
    Get current VIX with fallback chain:
      IBKR → yfinance → stale cache → default 20.0

    Returns cached value if cache is still fresh (< 2 min old).
    """
    global _vix_cache, _vix_cache_time

    # Return cache if still fresh
    if (
        _vix_cache is not None
        and _vix_cache_time is not None
        and (datetime.utcnow() - _vix_cache_time).total_seconds() < _CACHE_TTL_SECS
    ):
        return _vix_cache

    # Try IBKR first
    price = _get_vix_from_ibkr()
    if price:
        _vix_cache = price
        _vix_cache_time = datetime.utcnow()
        logger.info(f"VIX (IBKR): {price:.1f}")
        return price

    # Try yfinance second
    price = _get_vix_from_yfinance()
    if price:
        _vix_cache = price
        _vix_cache_time = datetime.utcnow()
        logger.info(f"VIX (yfinance): {price:.1f} — will retry IBKR next scan")
        return price

    # Use stale cache
    if _vix_cache is not None:
        logger.warning(f"VIX: all sources failed — using stale cache {_vix_cache:.1f}")
        return _vix_cache

    # Final fallback
    logger.warning(f"VIX: all sources failed — using default {_DEFAULT_VIX}")
    return _DEFAULT_VIX


def vix_regime_label(vix: float) -> str:
    """
    Classify VIX into market regime.
      < 15  → CALM
      15-20 → NORMAL
      20-30 → ELEVATED
      > 30  → FEAR
    """
    if vix < 15:
        return "CALM"
    elif vix < 20:
        return "NORMAL"
    elif vix < 30:
        return "ELEVATED"
    else:
        return "FEAR"
