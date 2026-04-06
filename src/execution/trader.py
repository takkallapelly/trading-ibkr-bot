"""
src/execution/trader.py
────────────────────────────────────────────────────────────────
LiveTrader — IBKR order management with bracket order protection.

Handles:
  - Connecting / disconnecting from TWS
  - Placing bracket orders (entry + stop + target simultaneously)
  - Tracking open positions and order states
  - Reconnection on disconnect
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from loguru import logger

try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract
    from ibapi.order import Order
    from ibapi.tag_value import TagValue
    IBAPI_AVAILABLE = True
except ImportError:
    IBAPI_AVAILABLE = False
    logger.warning("ibapi not installed — running in simulation mode")


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class Position:
    ticker:       str
    shares:       int
    entry_price:  float
    stop_price:   float
    target_price: float
    direction:    str        = "LONG"
    parent_id:    int        = 0
    stop_id:      int        = 0
    target_id:    int        = 0
    opened_at:    datetime   = field(default_factory=datetime.now)
    status:       str        = "OPEN"   # OPEN / CLOSED / PENDING


@dataclass
class Signal:
    ticker:       str
    close:        float
    stop:         float
    target:       float
    atr:          float
    score:        float
    direction:    str  = "LONG"


# ── IBKR Wrapper ───────────────────────────────────────────────────────────────

class _IBWrapper(EWrapper if IBAPI_AVAILABLE else object):
    """Minimal IBKR wrapper — captures connection events and order status."""

    def __init__(self):
        if IBAPI_AVAILABLE:
            EWrapper.__init__(self)
        self._next_order_id: int = 1
        self._connected: bool    = False
        self._order_id_ready     = threading.Event()

    # Connection events
    def nextValidId(self, orderId: int):
        self._next_order_id = orderId
        self._connected     = True
        self._order_id_ready.set()
        logger.info(f"TWS connected — next order ID: {orderId}")

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode in (2104, 2106, 2158):   # Market data farm warnings — ignore
            return
        if errorCode == 1100:
            logger.warning("TWS disconnected from IB servers")
            self._connected = False
        elif errorCode == 1102:
            logger.info("TWS reconnected to IB servers")
            self._connected = True
        else:
            logger.error(f"IBKR error {errorCode} | req={reqId} | {errorString}")

    def connectionClosed(self):
        self._connected = False
        logger.warning("TWS connection closed")

    def orderStatus(self, orderId, status, filled, remaining,
                   avgFillPrice, permId, parentId, lastFillPrice,
                   clientId, whyHeld, mktCapPrice):
        logger.info(f"Order {orderId} status={status} filled={filled} "
                   f"avgPrice={avgFillPrice:.2f}")

    def openOrder(self, orderId, contract, order, orderState):
        logger.debug(f"Open order: {orderId} {contract.symbol} {order.action} "
                    f"{order.totalQuantity} @ {order.orderType}")


# ── LiveTrader ──────────────────────────────────────────────────────────────────

class LiveTrader(_IBWrapper, EClient if IBAPI_AVAILABLE else object):
    """
    Main execution engine. Connects to TWS and places bracket orders.
    Safe even on internet disconnect — stops/targets held on IBKR servers.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 7497,
                 client_id: int = 1, paper: bool = True):
        if IBAPI_AVAILABLE:
            _IBWrapper.__init__(self)
            EClient.__init__(self, wrapper=self)

        self.host      = host
        self.port      = port
        self.client_id = client_id
        self.paper     = paper

        self.positions: dict[str, Position] = {}
        self._lock     = threading.Lock()
        self._thread:  Optional[threading.Thread] = None

    # ── Connection ─────────────────────────────────────────────────────────────

    def connect_and_run(self, timeout: int = 10) -> bool:
        """Connect to TWS and start message thread."""
        if not IBAPI_AVAILABLE:
            logger.warning("ibapi not available — simulation mode active")
            self._connected = True
            return True

        try:
            self.connect(self.host, self.port, self.client_id)
            self._thread = threading.Thread(target=self.run, daemon=True)
            self._thread.start()

            # Wait for nextValidId confirmation
            if not self._order_id_ready.wait(timeout=timeout):
                logger.error(f"TWS did not respond within {timeout}s — "
                            f"is TWS running on port {self.port}?")
                return False

            mode = "PAPER" if self.paper else "LIVE"
            logger.success(f"Connected to TWS | {mode} mode | port={self.port}")
            return True

        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

    def disconnect_safe(self):
        """Disconnect cleanly — open bracket orders remain on IBKR."""
        if IBAPI_AVAILABLE and self.isConnected():
            self.disconnect()
        logger.info("Disconnected from TWS — bracket orders remain active on IBKR")

    def is_connected(self) -> bool:
        if not IBAPI_AVAILABLE:
            return self._connected
        try:
            return self.isConnected() and self._connected
        except Exception:
            return False

    def next_order_id(self) -> int:
        oid = self._next_order_id
        self._next_order_id += 3   # Increment by 3 for bracket (parent+stop+target)
        return oid

    # ── Order placement ────────────────────────────────────────────────────────

    def place_bracket_order(self, signal: Signal, shares: int) -> dict:
        """
        Place bracket order: entry + stop + target as one atomic unit.

        IBKR holds all three legs server-side. Safe on disconnect.
        Even if your internet dies, IBKR will execute stop/target.

        Args:
            signal: Signal with ticker, stop, target prices
            shares: Number of shares (from RiskManager)

        Returns:
            dict with order IDs and confirmation
        """
        ticker       = signal.ticker
        entry_approx = signal.close
        stop_price   = round(signal.stop,   2)
        target_price = round(signal.target, 2)

        # Validate R:R
        risk   = abs(entry_approx - stop_price)
        reward = abs(target_price - entry_approx)
        if risk <= 0 or reward / risk < 1.0:
            logger.warning(f"{ticker}: R:R {reward/max(risk,0.001):.2f} < 1.0 — rejected")
            return {"status": "REJECTED", "reason": "insufficient R:R"}

        if self.paper or not IBAPI_AVAILABLE:
            return self._paper_bracket(ticker, shares, entry_approx,
                                       stop_price, target_price, signal.direction)

        return self._live_bracket(ticker, shares, entry_approx,
                                  stop_price, target_price, signal.direction)

    def _live_bracket(self, ticker, shares, entry_approx,
                      stop_price, target_price, direction) -> dict:
        """Submit real bracket to IBKR."""
        try:
            contract = self._make_contract(ticker)
            action      = "BUY"  if direction == "LONG" else "SELL"
            exit_action = "SELL" if direction == "LONG" else "BUY"

            parent_id = self.next_order_id()
            stop_id   = parent_id + 1
            target_id = parent_id + 2

            # Entry — market order with IBKR Adaptive algo
            parent = Order()
            parent.orderId       = parent_id
            parent.action        = action
            parent.orderType     = "MKT"
            parent.totalQuantity = shares
            parent.tif           = "DAY"
            parent.transmit      = False
            parent.algoStrategy  = "Adaptive"
            parent.algoParams    = [TagValue("adaptivePriority", "Normal")]

            # Stop loss — GTC, held on IBKR servers
            stop_leg = Order()
            stop_leg.orderId       = stop_id
            stop_leg.parentId      = parent_id
            stop_leg.action        = exit_action
            stop_leg.orderType     = "STP"
            stop_leg.auxPrice      = stop_price
            stop_leg.totalQuantity = shares
            stop_leg.tif           = "GTC"
            stop_leg.transmit      = False

            # Profit target — GTC, held on IBKR servers
            target_leg = Order()
            target_leg.orderId       = target_id
            target_leg.parentId      = parent_id
            target_leg.action        = exit_action
            target_leg.orderType     = "LMT"
            target_leg.lmtPrice      = target_price
            target_leg.totalQuantity = shares
            target_leg.tif           = "GTC"
            target_leg.transmit      = True   # Transmits all three legs

            self.placeOrder(parent_id, contract, parent)
            self.placeOrder(stop_id,   contract, stop_leg)
            self.placeOrder(target_id, contract, target_leg)

            # Track position
            with self._lock:
                self.positions[ticker] = Position(
                    ticker=ticker, shares=shares,
                    entry_price=entry_approx,
                    stop_price=stop_price,
                    target_price=target_price,
                    direction=direction,
                    parent_id=parent_id,
                    stop_id=stop_id,
                    target_id=target_id,
                )

            logger.success(
                f"✅ Bracket submitted | {ticker} | {shares}sh | "
                f"stop=${stop_price} target=${target_price} | "
                f"IDs: {parent_id}/{stop_id}/{target_id} | "
                f"SAFE: protected even if disconnected"
            )
            return {
                "status": "SUBMITTED", "ticker": ticker,
                "parent_id": parent_id, "stop_id": stop_id,
                "target_id": target_id,
            }

        except Exception as e:
            logger.error(f"Bracket order failed for {ticker}: {e}")
            return {"status": "ERROR", "reason": str(e)}

    def _paper_bracket(self, ticker, shares, entry_approx,
                       stop_price, target_price, direction) -> dict:
        """Paper trading — log bracket without submitting."""
        risk   = abs(entry_approx - stop_price)
        reward = abs(target_price - entry_approx)
        logger.info(
            f"[PAPER BRACKET] {ticker} | {direction} | {shares} shares\n"
            f"  Entry:  ~${entry_approx:.2f}\n"
            f"  Stop:    ${stop_price:.2f}  (risk=${risk*shares:.2f})\n"
            f"  Target:  ${target_price:.2f}  (reward=${reward*shares:.2f})\n"
            f"  R:R = {reward/max(risk,0.001):.2f}"
        )
        with self._lock:
            self.positions[ticker] = Position(
                ticker=ticker, shares=shares,
                entry_price=entry_approx,
                stop_price=stop_price,
                target_price=target_price,
                direction=direction,
            )
        return {
            "status": "PAPER", "ticker": ticker,
            "shares": shares, "stop": stop_price, "target": target_price,
        }

    def _make_contract(self, ticker: str) -> "Contract":
        c = Contract()
        c.symbol   = ticker
        c.secType  = "STK"
        c.exchange = "SMART"
        c.currency = "USD"
        return c

    # ── Position management ────────────────────────────────────────────────────

    def get_open_positions(self) -> dict[str, Position]:
        with self._lock:
            return {k: v for k, v in self.positions.items()
                    if v.status == "OPEN"}

    def mark_closed(self, ticker: str):
        with self._lock:
            if ticker in self.positions:
                self.positions[ticker].status = "CLOSED"
                logger.info(f"{ticker}: position marked CLOSED")

    def has_position(self, ticker: str) -> bool:
        with self._lock:
            return (ticker in self.positions and
                    self.positions[ticker].status == "OPEN")
