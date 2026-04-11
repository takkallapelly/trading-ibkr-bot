"""
Fix duplicate etradeOnly lines and clean up ibkr_client.py
Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_cleanup_ibkr.py
"""
path = r"src\execution\ibkr_client.py"

with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Remove duplicate etradeOnly/firmQuoteOnly lines — keep only first occurrence per order
cleaned = []
seen_etrade_parent = False
seen_etrade_stop   = False
seen_firm_stop     = False
skip_next          = False

i = 0
while i < len(lines):
    line = lines[i]

    # Track which order block we're in
    if 'parent = Order()' in line:
        seen_etrade_parent = False

    if 'stop_order  = Order()' in line or 'stop_order = Order()' in line:
        seen_etrade_stop = False
        seen_firm_stop   = False

    # Remove duplicate stop_order etradeOnly/firmQuoteOnly lines
    if 'stop_order.etradeOnly' in line:
        if seen_etrade_stop:
            i += 1
            continue  # skip duplicate
        seen_etrade_stop = True

    if 'stop_order.firmQuoteOnly' in line:
        if seen_firm_stop:
            i += 1
            continue  # skip duplicate
        seen_firm_stop = True

    cleaned.append(line)
    i += 1

with open(path, "w", encoding="utf-8") as f:
    f.writelines(cleaned)

# Verify
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

count = content.count("etradeOnly")
print(f"etradeOnly references after cleanup: {count} (should be 3)")

# Count per order
parent_count = content.count("parent.etradeOnly")
stop_count   = content.count("stop_order.etradeOnly")
tp_count     = content.count("tp_order.etradeOnly")
print(f"  parent:  {parent_count} (need 1)")
print(f"  stop:    {stop_count} (need 1)")
print(f"  tp:      {tp_count} (need 1)")

if parent_count == 1 and stop_count == 1 and tp_count == 1:
    print("\nSUCCESS: All 3 orders have exactly 1 etradeOnly=False")
    print("\nNow let's check if TWS needs a market data subscription.")
    print("In TWS: Account > Market Data Subscriptions")
    print("Make sure US Stocks (SMART) is enabled.")
else:
    print("\nWARNING: counts not as expected — check the file manually")

print("\nRestart the bot:")
print("  python scripts/run_live.py --paper")
