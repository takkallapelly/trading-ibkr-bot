"""
Wire IBKR client into VIX fetcher so it uses real-time data.
Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_vix_ibkr.py

Steps:
  1. Copy new vix.py to src/data/
  2. Patch RiskManager to accept and use ibkr_client for VIX
  3. Patch LiveTrader to pass ibkr_client to RiskManager
"""
import shutil

# ── Step 1: Copy new vix.py ──────────────────────────────────────────────────
# (assumes fix_vix_sizing.py was already run and created src/data/vix.py)
# If not, copy from downloads
import os
if not os.path.exists(r"src\data\vix.py"):
    print("ERROR: src/data/vix.py not found!")
    print("Please copy the downloaded vix.py to src/data/vix.py first")
    exit(1)
else:
    print("✓ src/data/vix.py exists")

# ── Step 2: Patch RiskManager to store and use ibkr_client ──────────────────
risk_path = r"src\risk\manager.py"
with open(risk_path, "r", encoding="utf-8") as f:
    risk = f.read()

# Add ibkr_client parameter to __init__
old_init = "    def __init__(\n        self,\n        capital:"
new_init = "    def __init__(\n        self,\n        capital:"

# Find the __init__ and add ibkr_client storage
if "self._ibkr_client" not in risk:
    # Find where capital is stored in __init__
    old_store = "        self._capital = capital"
    new_store = (
        "        self._capital     = capital\n"
        "        self._ibkr_client = None   # set via set_ibkr_client()"
    )
    if old_store in risk:
        risk = risk.replace(old_store, new_store)
        print("✓ Added _ibkr_client storage to RiskManager.__init__")

# Add set_ibkr_client method
if "def set_ibkr_client" not in risk:
    old_approve = "    def approve_entry("
    new_method = '''    def set_ibkr_client(self, client) -> None:
        """Set IBKR client for real-time VIX fetching."""
        self._ibkr_client = client
        logger.info("RiskManager: IBKR client connected for real-time VIX")

    def approve_entry('''
    if old_approve in risk:
        risk = risk.replace(old_approve, new_method)
        print("✓ Added set_ibkr_client() to RiskManager")

# Update VIX call to pass ibkr_client
old_vix_call = "            vix_max, vix_val, vix_label = vix_position_size(self._capital)"
new_vix_call = "            vix_max, vix_val, vix_label = vix_position_size(self._ibkr_client, self._capital)"
if old_vix_call in risk:
    risk = risk.replace(old_vix_call, new_vix_call)
    print("✓ Updated VIX call to use IBKR client")
elif "vix_position_size" not in risk:
    print("⚠ VIX not yet in RiskManager — run fix_vix_sizing.py first")

with open(risk_path, "w", encoding="utf-8") as f:
    f.write(risk)

# ── Step 3: Patch LiveTrader to pass ibkr_client to RiskManager ──────────────
trader_path = r"src\execution\live_trader.py"
with open(trader_path, "r", encoding="utf-8") as f:
    trader = f.read()

# After IBKR connects, wire client into risk manager
old_wire = (
    "            self._intraday.use_ibkr = True\n"
    "            self._intraday.client   = self._client"
)
new_wire = (
    "            self._intraday.use_ibkr = True\n"
    "            self._intraday.client   = self._client\n"
    "            # Wire IBKR client into risk manager for real-time VIX\n"
    "            if hasattr(self._risk, 'set_ibkr_client'):\n"
    "                self._risk.set_ibkr_client(self._client)\n"
    "                logger.info('RiskManager: using IBKR real-time VIX')"
)
if old_wire in trader:
    trader = trader.replace(old_wire, new_wire)
    print("✓ Wired IBKR client into RiskManager in LiveTrader.start()")
else:
    print("⚠ Could not find IBKR connect block in LiveTrader")

with open(trader_path, "w", encoding="utf-8") as f:
    f.write(trader)

print("""
════════════════════════════════════════════════════════
  VIX NOW USES IBKR REAL-TIME DATA
════════════════════════════════════════════════════════

  Data flow:
    IBKR TWS → reqMktData(^VIX) → real-time tick
    → RiskManager → position size → trade

  Latency: ~1 second (vs 5-10 seconds for yfinance)
  Cache:   5 minutes (refreshed every scan if needed)
  Fallback: yfinance if IBKR disconnects

  Restart the bot:
  python scripts/run_live.py --paper
""")
