"""
Fix child order TIF=GTC in ib_insync bracket orders.
Error 10349: child orders defaulting to DAY instead of GTC.
Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_tif_gtc.py
"""
import os

path = r"src\execution\ibkr_client.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Fix stop order TIF
old_sl = """        sl_ord          = StopOrder(exit_side, qty, round(stop, 2))
        sl_ord.parentId = parent.orderId
        sl_ord.tif      = "GTC"
        sl_ord.transmit = False"""

new_sl = """        sl_ord              = StopOrder(exit_side, qty, round(stop, 2))
        sl_ord.parentId     = parent.orderId
        sl_ord.tif          = "GTC"
        sl_ord.outsideRth   = False
        sl_ord.transmit     = False"""

# Fix take profit TIF
old_tp = """        tp_ord          = LimitOrder(exit_side, qty, round(target, 2))
        tp_ord.parentId = parent.orderId
        tp_ord.tif      = "GTC"
        tp_ord.transmit = True    # transmits all three"""

new_tp = """        tp_ord              = LimitOrder(exit_side, qty, round(target, 2))
        tp_ord.parentId     = parent.orderId
        tp_ord.tif          = "GTC"
        tp_ord.outsideRth   = False
        tp_ord.transmit     = True    # transmits all three"""

fixes = 0
if old_sl in content:
    content = content.replace(old_sl, new_sl)
    fixes += 1
    print("✓ Fixed stop order TIF=GTC")
else:
    print("⚠ Stop order pattern not found")

if old_tp in content:
    content = content.replace(old_tp, new_tp)
    fixes += 1
    print("✓ Fixed take profit order TIF=GTC")
else:
    print("⚠ Take profit pattern not found")

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

# Clear cache
cache = r"src\execution\__pycache__"
if os.path.exists(cache):
    for f in os.listdir(cache):
        if "ibkr_client" in f:
            os.remove(os.path.join(cache, f))
            print(f"✓ Cleared {f}")

if fixes == 2:
    print("\nSUCCESS: Both child orders now explicitly set TIF=GTC")
    print("Error 10349 will no longer appear")
else:
    print(f"\nPartial fix: {fixes}/2 patterns found")

print("\nRestart bot: python scripts/run_live.py --paper")
