"""
Fix IBKR error 10268: 'EtradeOnly' order attribute not supported.
Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_etrade.py

The ibapi Order() object has etradeOnly=True by default.
IBKR paper trading does not support this attribute.
Fix: explicitly set etradeOnly=False and firmQuoteOnly=False on all orders.
"""

path = r"src\execution\ibkr_client.py"

with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Fix parent order
old_parent = """        parent = Order()
        parent.orderId       = parent_id
        parent.action        = side          # "BUY" or "SELL"
        parent.orderType     = order_type
        parent.totalQuantity = qty
        parent.tif           = "DAY"
        parent.transmit      = False         # don't send until children are ready
        if order_type == "LMT":
            parent.lmtPrice  = round(entry, 2)"""

new_parent = """        parent = Order()
        parent.orderId       = parent_id
        parent.action        = side          # "BUY" or "SELL"
        parent.orderType     = order_type
        parent.totalQuantity = qty
        parent.tif           = "DAY"
        parent.transmit      = False         # don't send until children are ready
        parent.etradeOnly    = False         # FIX: default is True, not supported
        parent.firmQuoteOnly = False         # FIX: default is True, not supported
        if order_type == "LMT":
            parent.lmtPrice  = round(entry, 2)"""

# Fix stop order
old_stop = """        stop_order  = Order()
        stop_order.orderId       = stop_id
        stop_order.action        = stop_action
        stop_order.orderType     = "STP"
        stop_order.auxPrice      = round(stop, 2)
        stop_order.totalQuantity = qty
        stop_order.parentId      = parent_id
        stop_order.tif           = "GTC"     # CRITICAL: must be GTC (fixes Error 10349)
        stop_order.transmit      = False"""

new_stop = """        stop_order  = Order()
        stop_order.orderId       = stop_id
        stop_order.action        = stop_action
        stop_order.orderType     = "STP"
        stop_order.auxPrice      = round(stop, 2)
        stop_order.totalQuantity = qty
        stop_order.parentId      = parent_id
        stop_order.tif           = "GTC"     # CRITICAL: must be GTC (fixes Error 10349)
        stop_order.transmit      = False
        stop_order.etradeOnly    = False     # FIX: not supported
        stop_order.firmQuoteOnly = False     # FIX: not supported"""

# Fix take profit order
old_tp = """        tp_order  = Order()
        tp_order.orderId       = target_id
        tp_order.action        = tp_action
        tp_order.orderType     = "LMT"
        tp_order.lmtPrice      = round(target, 2)
        tp_order.totalQuantity = qty
        tp_order.parentId      = parent_id
        tp_order.tif           = "GTC"       # CRITICAL: must be GTC (fixes Error 10349)
        tp_order.transmit      = True         # this one transmits all three"""

new_tp = """        tp_order  = Order()
        tp_order.orderId       = target_id
        tp_order.action        = tp_action
        tp_order.orderType     = "LMT"
        tp_order.lmtPrice      = round(target, 2)
        tp_order.totalQuantity = qty
        tp_order.parentId      = parent_id
        tp_order.tif           = "GTC"       # CRITICAL: must be GTC (fixes Error 10349)
        tp_order.transmit      = True        # this one transmits all three
        tp_order.etradeOnly    = False       # FIX: not supported
        tp_order.firmQuoteOnly = False       # FIX: not supported"""

fixes = [
    (old_parent, new_parent, "parent order"),
    (old_stop,   new_stop,   "stop order"),
    (old_tp,     new_tp,     "take profit order"),
]

applied = []
for old, new, label in fixes:
    if old in content:
        content = content.replace(old, new)
        applied.append(label)
    else:
        print(f"⚠ Could not find {label} block — may already be fixed")

if applied:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"SUCCESS: Fixed etradeOnly on {len(applied)} orders: {', '.join(applied)}")
    print()
    print("Now restart the bot:")
    print("  python scripts/run_live.py --paper")
    print()
    print("Next trade will appear in IBKR TWS paper account order book.")
else:
    print("No changes made — checking current state:")
    for i, line in enumerate(content.split('\n')):
        if 'etradeOnly' in line:
            print(f"  Line {i+1}: {line.strip()}")
