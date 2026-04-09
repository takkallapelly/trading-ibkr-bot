"""
src/data/vix.py
───────────────
VIX fetcher using IBKR TWS (primary) with yfinance fallback.

Cache behaviour:
  - IBKR success  → cache 5 minutes (fast, reliable)
  - yfinance used → cache 1 minute only (retry IBKR sooner)
  - Every call checks if IBKR is now available if last was fallback
"""
from __future__ import annotations

import time
import threading
from loguru import logger

VIX_TIERS = [
    (35, 5000),
    (25, 4000),
    (20, 3000),
    (15, 2000),
    (0,  1000),
]

_vix_cache: dict = {
    "value":     None,
    "timestamp": 0,
    "source":    None,   # "ibkr" or "yfinance"
}

IBKR_CACHE_SECS    = 300   # 5 min if from IBKR
YFINANCE_CACHE_SECS = 60   # 1 min if from yfinance — retry IBKR sooner


def get_vix(ibkr_client=None) -> float:
    """
    Fetch VIX with smart caching:
    - If last source was IBKR: cache 5 min
    - If last source was yfinance: cache only 1 min, retry IBKR sooner
    - Always prefers IBKR when connected
    """
    global _vix_cache
    now = time.time()

    # Determine cache TTL based on last source
    cache_ttl = (
        IBKR_CACHE_SECS
        if _vix_cache.get("source") == "ibkr"
        else YFINANCE_CACHE_SECS
    )

    # Return cache if still valid
    if (
        _vix_cache["value"] is not None
        and now - _vix_cache["timestamp"] < cache_ttl
    ):
        return _vix_cache["value"]

    # Always try IBKR first — even if last attempt failed
    if ibkr_client and getattr(ibkr_client, "is_connected", lambda: False)():
        vix = _fetch_vix_ibkr(ibkr_client)
        if vix and vix > 0:
            _vix_cache = {"value": vix, "timestamp": now, "source": "ibkr"}
            logger.info(f"VIX (IBKR): {vix:.1f}")
            return vix
        else:
            logger.debug("IBKR VIX unavailable — falling back to yfinance")
    else:
        logger.debug("IBKR not connected — using yfinance for VIX")

    # Fallback: yfinance (cache only 1 min so we retry IBKR quickly)
    vix = _fetch_vix_yfinance()
    if vix and vix > 0:
        _vix_cache = {"value": vix, "timestamp": now, "source": "yfinance"}
        logger.info(f"VIX (yfinance): {vix:.1f} — will retry IBKR in 60s")
        return vix

    # Last resort: stale cache or default
    stale = _vix_cache.get("value")
    if stale:
        logger.warning(f"VIX: using stale cache {stale:.1f}")
        return stale

    logger.warning("VIX: all sources failed — using default 20.0")
    return 20.0


def _fetch_vix_ibkr(client) -> float | None:
    """Fetch real-time VIX from IBKR TWS."""
    try:
        from ibapi.contract import Contract

        contract          = Contract()
        contract.symbol   = "VIX"
        contract.secType  = "IND"
        contract.exchange = "CBOE"
        contract.currency = "USD"

        received  = threading.Event()
        vix_value = [None]
        req_id    = client.next_order_id()
        original  = getattr(client, "tickPrice", None)

        def on_tick(reqId, tickType, price, attrib):
            # tickType 4=last, 9=close, 1=bid, 2=ask
            if reqId == req_id and tickType in (1, 2, 4, 9) and price > 0:
                vix_value[0] = float(price)
                received.set()
            if original:
                original(reqId, tickType, price, attrib)

        client.tickPrice = on_tick
        client.reqMktData(req_id, contract, "", True, False, [])
        received.wait(timeout=5)

        try:
            client.cancelMktData(req_id)
        except Exception:
            pass

        if original:
            client.tickPrice = original

        return vix_value[0]

    except Exception as e:
        logger.debug(f"IBKR VIX error: {e}")
        return None


def _fetch_vix_yfinance() -> float | None:
    """Fetch VIX from yfinance as fallback."""
    try:
        import yfinance as yf
        data = yf.download(
            "^VIX", period="5d", interval="1h",
            progress=False, threads=False
        )
        if not data.empty:
            val = data["Close"].iloc[-1]
            return float(val.iloc[0]) if hasattr(val, "iloc") else float(val)
    except Exception as e:
        logger.debug(f"yfinance VIX error: {e}")
    return None


def vix_position_size(
    ibkr_client=None,
    capital: float = 25000,
) -> tuple[float, float, str]:
    """Return (max_position_usd, vix_value, tier_label)."""
    vix = get_vix(ibkr_client)
    labels = {
        5000: "EXTREME FEAR (VIX>35) — maximum size",
        4000: "HIGH VOLATILITY (VIX 25-35) — large size",
        3000: "ELEVATED (VIX 20-25) — normal size",
        2000: "NORMAL (VIX 15-20) — reduced size",
        1000: "LOW VOL (VIX<15) — minimum size",
    }
    for threshold, size in VIX_TIERS:
        if vix > threshold:
            return float(size), vix, labels[size]
    return 1000.0, vix, labels[1000]


def vix_regime_label(vix: float) -> str:
    if vix > 35: return "EXTREME FEAR"
    if vix > 25: return "HIGH VOL"
    if vix > 20: return "ELEVATED"
    if vix > 15: return "NORMAL"
    return "LOW VOL"
