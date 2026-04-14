"""
src/execution/ibkr_client.py
─────────────────────────────
Low-level IBKR API wrapper using ibapi (TWS API).

Handles the raw socket connection to TWS/Gateway, processes
callbacks, and maintains thread-safe order/position state.

Architecture:
  IBKRClient (this file) → wraps ibapi EClient + EWrapper
  LiveTrader (live_trader.py) → uses IBKRClient for high-level logic

TWS API is callback-based (event-driven). When you place an order,
TWS calls back: orderStatus(), execDetails(), error(), etc.
We use threading.Event to convert callbacks into synchronous waits.

Key issues this solves (from your v7 experience):
  - Error 10349: GTC flag missing on bracket child orders → fixed
  - Position state resetting before fill → fixed with state machine
  - CONFIRM_BARS impossible condition → removed entirely
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Callable

from loguru import logger

# Monkey-patch ibapi Order: etradeOnly="" = not sent in wire protocol
try:
    from ibapi.order import Order as _IBOrder
    _orig = _IBOrder.__init__
    def _new(self, *a, **k):
        _orig(self, *a, **k)
        self.etradeOnly    = ""
        self.firmQuoteOnly = ""
    _IBOrder.__init__ = _new
except Exception:
    pass

# ibapi must be installed manually from IBKR website
# See README.md § IBKR Setup
try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract
    from ibapi.order import Order
    from ibapi.common import OrderId, TickerId
    IBKR_AVAILABLE = True
except ImportError:
    IBKR_AVAILABLE = False
    logger.warning(
        "ibapi not installed — live trading disabled. "
        "Install from IBKR website. See README § IBKR Setup."
    )
    # Create stub classes so the rest of the code doesn't crash
    class EWrapper: pass
    class EClient:
        def __init__(self, wrapper): pass

from src.config import settings


class IBKRClient(EWrapper, EClient):
    """
    Thread-safe IBKR API client.

    Connects to TWS/Gateway and processes all API callbacks.
    Maintains internal state for orders, positions, and account data.

    Usage:
        client = IBKRClient()
        client.connect_and_run()

        # Place a bracket order
        parent_id = client.next_order_id()
        client.place_bracket_order(ticker, qty, entry, stop, target)

        # Disconnect when done
        client.disconnect()
    """

    def __init__(self):
        EWrapper.__init__(self)
        EClient.__init__(self, wrapper=self)

        # Threading
        self._lock           = threading.Lock()
        self._connected      = threading.Event()
        self._next_id_event  = threading.Event()
        self._api_thread: threading.Thread | None = None

        # State
        self._next_order_id : int  = -1
        self._positions     : dict = {}   # {ticker: position_dict}
        self._orders        : dict = {}   # {order_id: order_dict}
        self._account_value : float = 0.0
        self._callbacks     : dict[str, list[Callable]] = {}

    # ── Connection ────────────────────────────────────────────────────────────

    def connect_and_run(
        self,
        host: str | None = None,
        port: int | None = None,
        client_id: int | None = None,
        timeout: float = 10.0,
    ) -> bool:
        """
        Connect to TWS/Gateway and start the message loop in a background thread.

        Args:
            host      : TWS host (default from .env)
            port      : TWS port (default from .env)
            client_id : unique client ID (default from .env)
            timeout   : seconds to wait for connection

        Returns:
            True if connected successfully.
        """
        if not IBKR_AVAILABLE:
            logger.error("ibapi not installed — cannot connect")
            return False

        host      = host      or settings.IBKR_HOST
        port      = port      or settings.IBKR_PORT
        client_id = client_id or settings.IBKR_CLIENT_ID

        logger.info(f"Connecting to IBKR | {host}:{port} | client_id={client_id}")

        try:
            self.connect(host, port, client_id)
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

        # Start API message loop in background thread
        self._api_thread = threading.Thread(
            target=self.run,
            name="ibkr-api",
            daemon=True,
        )
        self._api_thread.start()

        # Wait for connection confirmation (nextValidId callback)
        if not self._connected.wait(timeout=timeout):
            logger.error(f"Connection timed out after {timeout}s — is TWS open?")
            return False

        logger.success(
            f"Connected to IBKR | {host}:{port} | "
            f"next_order_id={self._next_order_id}"
        )
        # Request fresh order ID from IBKR
        self.reqIds(-1)
        return True

    def is_connected(self) -> bool:
        return self._connected.is_set() and self.isConnected()

    # ── EWrapper Callbacks ────────────────────────────────────────────────────

    def nextValidId(self, orderId: int) -> None:
        """Called by IBKR with next valid order ID. Sync counter always."""
        with self._lock:
            if orderId > self._next_order_id:
                logger.info(f"Order ID synced: {self._next_order_id} -> {orderId}")
                self._next_order_id = orderId
        self._connected.set()
        logger.debug(f"nextValidId: {orderId}")

    def error(self, reqId: int, errorCode: int, errorString: str,
              advancedOrderRejectJson: str = "") -> None:
        """
        TWS error callback.

        Key error codes:
          2104, 2106, 2158 — market data farm connected (informational, ignore)
          10349             — OCA group child order rejected (bracket issue)
          201               — order rejected
          504               — not connected
        """
        # Informational codes — not real errors
        # 2103/2105/2110: market data farms broken (normal outside market hours)
        IGNORE_CODES = {2104, 2106, 2158, 2119, 2157, 2176, 2103, 2105, 2110}
        if errorCode in IGNORE_CODES:
            logger.debug(f"IBKR info {errorCode}: {errorString}")
            return

        if errorCode == 10349:
            # GTC flag missing on child orders — fixed in place_bracket_order
            logger.warning(
                f"Error 10349 on order {reqId}: {errorString} — "
                "check that child orders have tif='GTC'"
            )
        elif errorCode == 201:
            logger.error(f"Order {reqId} REJECTED: {errorString}")
            self._update_order_status(reqId, "REJECTED")
        elif errorCode == 504:
            logger.error("Not connected to TWS — reconnecting...")
            self._connected.clear()
        else:
            logger.warning(f"IBKR error {errorCode} (req={reqId}): {errorString}")

        self._fire_callback("error", reqId=reqId, code=errorCode, msg=errorString)

    def orderStatus(
        self, orderId: int, status: str, filled: float,
        remaining: float, avgFillPrice: float, permId: int,
        parentId: int, lastFillPrice: float, clientId: int,
        whyHeld: str, mktCapPrice: float,
    ) -> None:
        """Called whenever an order's status changes."""
        with self._lock:
            if orderId in self._orders:
                self._orders[orderId].update({
                    "status":         status,
                    "filled":         filled,
                    "remaining":      remaining,
                    "avg_fill_price": avgFillPrice,
                    "last_updated":   datetime.utcnow().isoformat(),
                })

        logger.info(
            f"Order {orderId} | status={status} | "
            f"filled={filled} @ ${avgFillPrice:.2f} | remaining={remaining}"
        )
        self._fire_callback("order_status", order_id=orderId, status=status,
                            filled=filled, avg_fill=avgFillPrice)

    def execDetails(self, reqId: int, contract, execution) -> None:
        """Called when an order gets a fill."""
        logger.info(
            f"Fill | {contract.symbol} | "
            f"side={execution.side} qty={execution.shares} "
            f"price=${execution.price:.2f} | execId={execution.execId}"
        )
        self._fire_callback(
            "fill",
            ticker=contract.symbol,
            side=execution.side,
            qty=execution.shares,
            price=execution.price,
            exec_id=execution.execId,
        )

    def position(self, account: str, contract, pos: float, avgCost: float) -> None:
        """Called with current position data."""
        with self._lock:
            if pos != 0:
                self._positions[contract.symbol] = {
                    "ticker":   contract.symbol,
                    "position": pos,
                    "avg_cost": avgCost,
                }
            elif contract.symbol in self._positions:
                del self._positions[contract.symbol]

    def positionEnd(self) -> None:
        logger.debug(f"Positions loaded: {list(self._positions.keys())}")

    def accountSummary(self, reqId: int, account: str,
                       tag: str, value: str, currency: str) -> None:
        if tag == "NetLiquidation":
            try:
                self._account_value = float(value)
            except ValueError:
                pass

    def connectionClosed(self) -> None:
        logger.warning("IBKR connection closed")
        self._connected.clear()

    # ── Order placement ───────────────────────────────────────────────────────

    def next_order_id(self) -> int:
        """Get and increment the next valid order ID."""
        with self._lock:
            oid = self._next_order_id
            self._next_order_id += 1
        return oid

    def place_bracket_order(
        self,
        ticker:    str,
        qty:       int,
        entry:     float,
        stop:      float,
        target:    float,
        side:      str = "BUY",
        order_type:str = "MKT",
    ) -> tuple[int, int, int]:
        """
        Place a bracket order using ib_insync order objects.

        ib_insync MarketOrder/StopOrder/LimitOrder do NOT have the
        etradeOnly field — this permanently fixes error 10268.

        This is the same approach used in ibkr_final_bot_v10.py
        which successfully placed bracket orders in paper trading.
        """
        if not self.is_connected():
            raise RuntimeError("Not connected to TWS")

        try:
            from ib_insync import MarketOrder, StopOrder, LimitOrder
        except ImportError:
            raise RuntimeError("pip install ib_insync")

        contract    = self._make_contract(ticker)
        exit_side   = "SELL" if side == "BUY" else "BUY"

        # ── Parent: market order ─────────────────────────────────────────────
        parent          = MarketOrder(side, qty)
        parent.orderId  = self.next_order_id()
        parent.tif      = "DAY"   # explicit DAY silences error 10349
        parent.transmit = False   # hold until children are registered

        # ── Stop loss child ─────────────────────────────────────────────────
        sl_ord          = StopOrder(exit_side, qty, round(stop, 2))
        sl_ord.parentId = parent.orderId
        sl_ord.tif      = "GTC"
        sl_ord.transmit = False

        # ── Take profit child ────────────────────────────────────────────────
        tp_ord          = LimitOrder(exit_side, qty, round(target, 2))
        tp_ord.parentId = parent.orderId
        tp_ord.tif      = "GTC"
        tp_ord.transmit = True    # transmits all three

        # Place all three — capture each order ID explicitly
        self.placeOrder(parent.orderId, contract, parent)
        stop_id = self.next_order_id()
        self.placeOrder(stop_id, contract, sl_ord)
        tp_id = self.next_order_id()
        self.placeOrder(tp_id, contract, tp_ord)

        parent_id = parent.orderId
        target_id = tp_id

        # Register in state tracker
        with self._lock:
            for oid, action in [(parent_id, side), (stop_id, exit_side),
                                (target_id, exit_side)]:
                self._orders[oid] = {
                    "order_id": oid,
                    "ticker":   ticker,
                    "action":   action,
                    "qty":      qty,
                    "status":   "PENDING",
                }

        logger.info(
            f"Bracket order placed (ib_insync) | {ticker} {side} {qty}sh | "
            f"entry~{entry:.2f} stop={stop:.2f} target={target:.2f} | "
            f"ids=({parent_id},{stop_id},{target_id})"
        )

        return parent_id, stop_id, target_id
    def cancel_order(self, order_id: int) -> None:
        """Cancel a specific order."""
        if not IBKR_AVAILABLE:
            return
        self.cancelOrder(order_id, "")
        logger.info(f"Cancelled order {order_id}")

    def cancel_all_orders(self) -> None:
        """Cancel all open orders (emergency stop)."""
        if not IBKR_AVAILABLE:
            return
        self.reqGlobalCancel()
        logger.warning("All orders cancelled (reqGlobalCancel)")

    def request_positions(self) -> None:
        """Request current positions from TWS."""
        if IBKR_AVAILABLE and self.is_connected():
            self.reqPositions()

    def request_account_summary(self) -> None:
        """Request account summary (balance, net liquidation, etc.)."""
        if IBKR_AVAILABLE and self.is_connected():
            self.reqAccountSummary(1, "All", "NetLiquidation,TotalCashValue")

    # ── State access ──────────────────────────────────────────────────────────

    def get_positions(self) -> dict:
        with self._lock:
            return dict(self._positions)

    def get_orders(self) -> dict:
        with self._lock:
            return dict(self._orders)

    def get_order(self, order_id: int) -> dict | None:
        with self._lock:
            return self._orders.get(order_id)

    def get_account_value(self) -> float:
        return self._account_value

    def is_order_filled(self, order_id: int) -> bool:
        order = self.get_order(order_id)
        return order is not None and order.get("status") == "Filled"

    def is_order_active(self, order_id: int) -> bool:
        order = self.get_order(order_id)
        if order is None:
            return False
        return order.get("status") not in ("Filled","Cancelled","Inactive","REJECTED")

    # ── Callback system ───────────────────────────────────────────────────────

    def on(self, event: str, callback: Callable) -> None:
        """Register a callback for an event (fill, order_status, error)."""
        self._callbacks.setdefault(event, []).append(callback)

    def _fire_callback(self, event: str, **kwargs) -> None:
        for cb in self._callbacks.get(event, []):
            try:
                cb(**kwargs)
            except Exception as e:
                logger.warning(f"Callback error ({event}): {e}")

    # ── Private helpers ───────────────────────────────────────────────────────

    def _make_contract(self, ticker: str) -> "Contract":
        c           = Contract()
        c.symbol    = ticker
        c.secType   = "STK"
        c.exchange  = "SMART"
        c.currency  = "USD"
        return c

    def _update_order_status(self, order_id: int, status: str) -> None:
        with self._lock:
            if order_id in self._orders:
                self._orders[order_id]["status"] = status
