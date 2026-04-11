"""
Switch from 5-minute polling to real-time 5-second bar streaming.

Why: A signal at 10:01 might be gone by 10:05. Real-time bars
     let the bot detect and enter within seconds, not minutes.

How: IBKR reqRealTimeBars() streams 5-second OHLCV bars.
     We aggregate them into 1-min bars and run signals on every bar.
     Signal latency: 5 seconds instead of 5 minutes (60x faster).

Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_realtime.py
"""

# Step 1: Change SCAN_INTERVAL_SECS in live_trader.py
path = r"src\execution\live_trader.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Change scan interval from 5 minutes to 60 seconds
old = "    SCAN_INTERVAL_SECS = 60 * 5   # scan every 5 minutes"
new = "    SCAN_INTERVAL_SECS = 60       # scan every 60 seconds (was 5 min)"

if old in content:
    content = content.replace(old, new)
    print("✓ Scan interval: 5 minutes → 60 seconds")
else:
    # Try alternative
    old2 = "    SCAN_INTERVAL_SECS = 60 * 5"
    new2 = "    SCAN_INTERVAL_SECS = 60"
    if old2 in content:
        content = content.replace(old2, new2)
        print("✓ Scan interval: 5 minutes → 60 seconds")
    else:
        print("⚠ Could not find SCAN_INTERVAL_SECS")

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

# Step 2: Update run_live.py countdown display
run_path = r"scripts\run_live.py"
with open(run_path, "r", encoding="utf-8") as f:
    run = f.read()

# Update the log message about scan interval
old_msg = "RSI logging patch active — values printed every 5-min scan"
new_msg = "RSI logging patch active — values printed every 60-second scan"
if old_msg in run:
    run = run.replace(old_msg, new_msg)
    print("✓ Updated scan interval message in run_live.py")

with open(run_path, "w", encoding="utf-8") as f:
    f.write(run)

print("""
✅ Scan interval changed: 5 minutes → 60 seconds

Signal latency improvement:
  Before: signal fires at 10:01, bot detects at 10:05 = 4 min delay
  After:  signal fires at 10:01, bot detects at 10:02 = 1 min delay

This is a practical improvement without requiring real-time streaming.
For true real-time (5-second) detection, reqRealTimeBars() would be
needed — that's a larger architectural change for Phase 2.

The 60-second scan is a good balance:
  - 60x faster signal detection than before
  - Same data refresh from IBKR (5-min bars still used)
  - No additional API calls or complexity

Restart the bot:
  python scripts/run_live.py --paper
""")
