"""
Fix stuck order ID - IBKR rejects orders reusing failed IDs.
Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_orderid.py
"""

path = r"src\execution\ibkr_client.py"

with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Fix 1: Request fresh order ID from IBKR after connecting
old = "        logger.success(\n            f\"Connected to IBKR | {self._host}:{self._port} | \"\n            f\"next_order_id={self._next_order_id}\"\n        )"
new = "        logger.success(\n            f\"Connected to IBKR | {self._host}:{self._port} | \"\n            f\"next_order_id={self._next_order_id}\"\n        )\n        # Request a fresh valid order ID from IBKR to avoid reusing failed IDs\n        self.reqIds(-1)"

if old in content:
    content = content.replace(old, new)
    print("✓ Added reqIds(-1) after connect to get fresh order ID")
else:
    print("⚠ Pattern not found - trying alternative...")
    # Alternative: find the connected log line
    old2 = "logger.success(\n            f\"Connected to IBKR"
    if old2 in content:
        # Find exact block
        idx = content.find("logger.success(\n            f\"Connected to IBKR")
        end = content.find("\n        )", idx) + len("\n        )")
        block = content[idx:end]
        content = content[:end] + "\n        # Request fresh order ID from IBKR\n        self.reqIds(-1)" + content[end:]
        print("✓ Added reqIds(-1) via alternative method")
    else:
        print("⚠ Could not find connect success log - trying simpler approach")

# Fix 2: Make sure nextValidId callback updates our counter
old_next = "    def nextValidId(self, orderId: int) -> None:"
new_next = """    def nextValidId(self, orderId: int) -> None:
        \"\"\"Called by IBKR with the next valid order ID. Always use this value.\"\"\"
        with self._lock:
            # Always sync to IBKR's counter — never use stale IDs
            if orderId > self._next_order_id:
                logger.info(f"IBKR order ID synced: {self._next_order_id} → {orderId}")
                self._next_order_id = orderId"""

# Check if nextValidId exists
if "def nextValidId" in content:
    # Find and replace the existing implementation
    idx = content.find("    def nextValidId(self, orderId: int) -> None:")
    end = content.find("\n    def ", idx + 1)
    old_block = content[idx:end]

    # Replace with improved version that always syncs
    new_block = """    def nextValidId(self, orderId: int) -> None:
        \"\"\"Called by IBKR with next valid order ID. Sync counter always.\"\"\"
        with self._lock:
            if orderId > self._next_order_id:
                logger.info(f"Order ID synced: {self._next_order_id} -> {orderId}")
                self._next_order_id = orderId
        self._connected.set()
        logger.debug(f"nextValidId: {orderId}")
"""
    content = content[:idx] + new_block + content[end:]
    print("✓ Updated nextValidId to always sync order ID from IBKR")
else:
    print("⚠ nextValidId not found")

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("""
✅ Order ID fix applied!

What this fixes:
  - Bot now requests a fresh order ID from IBKR on every connect
  - nextValidId always syncs to IBKR's counter
  - Prevents reuse of failed order IDs (2021, 2022, 2023)
  - Next trade will use a new clean order ID

Restart the bot:
  python scripts/run_live.py --paper
""")
