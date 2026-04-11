"""
Fix paper orders to appear in IBKR TWS order book.
Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_paper_orders.py

Change: remove self.is_live condition from _place_order so that
        paper orders are sent to IBKR paper TWS (port 7497) and
        appear in the TWS order book like real trades — but with
        no real money at risk.
"""

path = r"C:\IBKR Bot\trading-bot\src\execution\live_trader.py"

with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# The current condition blocks paper orders from going to IBKR
old = "        if self.is_live and self._client and self._client.is_connected():"

# New condition: send to IBKR whenever connected (paper or live)
new = "        if self._client and self._client.is_connected():  # paper or live — IBKR handles the mode"

if old in content:
    content = content.replace(old, new)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("SUCCESS: Paper orders will now appear in IBKR TWS order book!")
    print()
    print("How it works:")
    print("  Port 7497 (paper TWS) -> IBKR simulates the order -> visible in TWS -> no real money")
    print("  Port 7496 (live TWS)  -> IBKR executes the order  -> visible in TWS -> real money")
    print()
    print("Restart the bot: python scripts/run_live.py --paper")
else:
    print("Pattern not found. Current _place_order condition:")
    for i, line in enumerate(content.split('\n')):
        if 'is_live and self._client' in line or '_place_order' in line:
            print(f"  Line {i+1}: {line.strip()}")
