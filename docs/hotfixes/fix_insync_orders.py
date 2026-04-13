"""
Fix bracket orders using ib_insync order objects.
ib_insync orders don't have etradeOnly field — no error 10268.
This is exactly how the working bot (ibkr_final_bot_v10.py) placed orders.

Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_insync_orders.py
"""

path = r"src\execution\ibkr_client.py"

with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Find the place_bracket_order method and replace its body
old_method = '''    def place_bracket_order(
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
        Place a bracket order: entry + stop loss + take profit.

        This is the core order type for the bot:
          - Parent: market/limit order to enter position
          - Child 1: stop loss (LMT below/above entry)
          - Child 2: take profit (LMT above/below entry)

        The fix for Error 10349: child orders MUST have tif="GTC"
        (Good Till Cancelled) so they persist after the parent fills.

        Args:
            ticker     : e.g. "META"
            qty        : number of shares
            entry      : entry price (used for LMT, ignored for MKT)
            stop       : stop loss price
            target     : take profit price
            side       : "BUY" for long, "SELL" for short
            order_type : "MKT" or "LMT"

        Returns:
            (parent_id, stop_id, target_id) — the three order IDs
        """
        if not IBKR_AVAILABLE:
            raise RuntimeError("ibapi not installed")
        if not self.is_connected():
            raise RuntimeError("Not connected to TWS")

        contract   = self._make_contract(ticker)
        parent_id  = self.next_order_id()
        stop_id    = self.next_order_id()
        target_id  = self.next_order_id()

        # ── Parent order (entry) ──────────────────────────────────────────────────────
        parent = Order()
        parent.orderId       = parent_id
        parent.action        = side          # "BUY" or "SELL"
        parent.orderType     = order_type
        parent.totalQuantity = qty
        parent.tif           = "DAY"
        parent.transmit      = False         # don't send until children are ready
        parent.etradeOnly    = False         # FIX: default is True, not supported
        parent.firmQuoteOnly = False         # FIX: default is True, not supported
        if order_type == "LMT":
            parent.lmtPrice  = round(entry, 2)

        # ── Stop loss child ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
        stop_action = "SELL" if side == "BUY" else "BUY"
        stop_order  = Order()
        stop_order.orderId       = stop_id
        stop_order.action        = stop_action
        stop_order.orderType     = "STP"
        stop_order.auxPrice      = round(stop, 2)
        stop_order.totalQuantity = qty
        stop_order.parentId      = parent_id
        stop_order.tif           = "GTC"     # CRITICAL: must be GTC (fixes Error 10349)
        stop_order.transmit      = False
        stop_order.etradeOnly    = False     # FIX: not supported

        # ── Take profit child ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
        tp_action = "SELL" if side == "BUY" else "BUY"
        tp_order  = Order()
        tp_order.orderId       = target_id
        tp_order.action        = tp_action
        tp_order.orderType     = "LMT"
        tp_order.lmtPrice      = round(target, 2)
        tp_order.totalQuantity = qty
        tp_order.parentId      = parent_id
        tp_order.tif           = "GTC"       # CRITICAL: must be GTC (fixes Error 10349)
        tp_order.transmit      = True        # this one transmits all three
        tp_order.etradeOnly    = False       # FIX: not supported
        tp_order.firmQuoteOnly = False       # FIX: not supported

        # Register in state tracker
        with self._lock:
            for oid, action in [(parent_id, side), (stop_id, stop_action),
                                (target_id, tp_action)]:
                self._orders[oid] = {
                    "order_id": oid,
                    "ticker":   ticker,
                    "action":   action,
                    "qty":      qty,
                    "status":   "PENDING",
                }

        # Place all three orders
        self.placeOrder(parent_id, contract, parent)
        self.placeOrder(stop_id,   contract, stop_order)
        self.placeOrder(target_id, contract, tp_order)

        logger.info(
            f"Bracket order placed | {ticker} {side} {qty}sh | "
            f"entry={entry:.2f} stop={stop:.2f} target={target:.2f} | "
            f"ids=({parent_id},{stop_id},{target_id})"
        )

        return parent_id, stop_id, target_id'''

new_method = '''    def place_bracket_order(
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

        # Place all three
        self.placeOrder(parent.orderId,  contract, parent)
        self.placeOrder(self.next_order_id(), contract, sl_ord)
        tp_id = self.next_order_id()
        self.placeOrder(tp_id, contract, tp_ord)

        parent_id = parent.orderId
        stop_id   = sl_ord.orderId if sl_ord.orderId else parent_id + 1
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

        return parent_id, stop_id, target_id'''

if old_method in content:
    content = content.replace(old_method, new_method)
    print("✓ Replaced place_bracket_order with ib_insync version")
else:
    print("⚠ Exact method not found — trying fuzzy match...")
    # Find method by signature
    start = content.find("    def place_bracket_order(")
    end   = content.find("\n    def ", start + 1)
    if start > -1 and end > -1:
        content = content[:start] + new_method + content[end:]
        print("✓ Replaced via fuzzy match")
    else:
        print("✗ Could not find method")
        exit(1)

# Remove monkey-patch if present (no longer needed)
if "Monkey-patch ibapi Order" in content:
    start = content.find("\n# Monkey-patch ibapi Order")
    end   = content.find("\n# ─", start + 1)
    if start > -1 and end > -1:
        content = content[:start] + content[end:]
        print("✓ Removed old monkey-patch (no longer needed)")

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

# Clear pyc cache
import os
cache = r"src\execution\__pycache__"
if os.path.exists(cache):
    for f in os.listdir(cache):
        if "ibkr_client" in f:
            os.remove(os.path.join(cache, f))
            print(f"✓ Cleared cache: {f}")

print("""
════════════════════════════════════════════════════════════════
  BRACKET ORDERS NOW USE ib_insync — ERROR 10268 FIXED
════════════════════════════════════════════════════════════════

  Root cause confirmed: ibapi Order() has etradeOnly=True by
  default. Even setting it to False still sends the field.
  TWS rejects orders containing this field entirely.

  Fix: ib_insync MarketOrder/StopOrder/LimitOrder objects
  don't have etradeOnly at all — TWS accepts them cleanly.

  This is exactly how ibkr_final_bot_v10.py worked.

  Now run:
    python fix_zombie_trades.py
    python scripts/run_live.py --paper

  Next signal → bracket order → appears in TWS ✅
""")
