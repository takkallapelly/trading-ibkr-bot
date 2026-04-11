"""
Direct fix for etradeOnly error on all three bracket orders.
Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_etrade2.py
"""

path = r"src\execution\ibkr_client.py"

with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Count existing fixes
existing = content.count("etradeOnly    = False")
print(f"Found {existing} existing etradeOnly fixes")

# Strategy: find every Order() block and ensure etradeOnly=False is set
# We'll do this by finding each order object assignment and adding the fix

fixes = 0

# Fix parent order — find transmit=False for parent and add after it
old1 = "        parent.transmit      = False         # don't send until children are ready"
new1 = "        parent.transmit      = False         # don't send until children are ready\n        parent.etradeOnly    = False         # FIX: not supported by paper TWS\n        parent.firmQuoteOnly = False         # FIX: not supported by paper TWS"
if old1 in content and "parent.etradeOnly" not in content:
    content = content.replace(old1, new1)
    fixes += 1
    print("✓ Fixed parent order")

# Fix stop order — find transmit=False for stop
old2 = "        stop_order.transmit      = False\n        stop_order.etradeOnly"
if old2 not in content:
    old2b = "        stop_order.transmit      = False"
    new2b = "        stop_order.transmit      = False\n        stop_order.etradeOnly    = False     # FIX: not supported\n        stop_order.firmQuoteOnly = False     # FIX: not supported"
    if old2b in content and "stop_order.etradeOnly" not in content:
        content = content.replace(old2b, new2b)
        fixes += 1
        print("✓ Fixed stop order")
    else:
        print(f"  stop_order already fixed or pattern not found")

# Fix take profit order — find transmit=True for tp
old3 = "        tp_order.transmit      = True        # this one transmits all three\n        tp_order.etradeOnly"
if old3 not in content:
    # Try variations
    for tp_transmit in [
        "        tp_order.transmit      = True         # this one transmits all three",
        "        tp_order.transmit      = True        # this one transmits all three",
        "        tp_order.transmit      = True",
    ]:
        if tp_transmit in content and "tp_order.etradeOnly" not in content:
            new_tp = tp_transmit + "\n        tp_order.etradeOnly    = False       # FIX: not supported\n        tp_order.firmQuoteOnly = False       # FIX: not supported"
            content = content.replace(tp_transmit, new_tp)
            fixes += 1
            print("✓ Fixed take profit order")
            break
    else:
        print("  tp_order already fixed or pattern not found")

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

# Verify
total = content.count("etradeOnly    = False") + content.count("etradeOnly    = False")
final_count = content.count("etradeOnly")
print(f"\nTotal etradeOnly references in file: {final_count}")
print(f"Applied {fixes} new fixes")

if final_count >= 3:
    print("\n✅ All 3 orders fixed — restart the bot:")
    print("   python scripts/run_live.py --paper")
else:
    print(f"\n⚠ Only {final_count} fixes found — showing ibkr_client place_bracket_order:")
    in_method = False
    for i, line in enumerate(content.split('\n')):
        if 'place_bracket_order' in line:
            in_method = True
        if in_method:
            print(f"  {i+1:3}: {line}")
        if in_method and i > 0 and 'return parent_id' in line:
            break
