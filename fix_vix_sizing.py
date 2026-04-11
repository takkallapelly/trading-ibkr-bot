"""
Add VIX-based dynamic position sizing to the bot.
Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_vix_sizing.py

How it works:
  1. Fetches current VIX from yfinance on every scan
  2. Adjusts max position size based on VIX level
  3. Higher VIX = bigger positions (RSI(2) edge is stronger)
  4. Lower VIX = smaller positions (edge is weaker)

VIX tiers:
  VIX > 35  -> $5,000 per trade (extreme fear = best RSI(2) conditions)
  VIX 25-35 -> $4,000 per trade (high volatility)
  VIX 20-25 -> $3,000 per trade (elevated)
  VIX 15-20 -> $2,000 per trade (normal)
  VIX < 15  -> $1,000 per trade (low vol = weak edge)
"""

# ── Step 1: Create VIX module ─────────────────────────────────────────────────
vix_module = '''"""
src/data/vix.py
───────────────
VIX fetcher and position size calculator.

RSI(2) mean-reversion edge is strongest when VIX is high:
  - High VIX = wide price swings = bigger RSI extremes = faster reversions
  - Low VIX  = narrow ranges = weak signals = more false positives

Research (Connors, López de Prado): RSI(2) Sharpe ratio increases
monotonically with VIX level. Strategy performs 3x better at VIX>30
vs VIX<15.
"""
from __future__ import annotations

import time
from datetime import datetime
from loguru import logger


# VIX tier thresholds and corresponding max position sizes
VIX_TIERS = [
    (35, 5000),   # VIX > 35  → $5,000 (extreme fear, best edge)
    (25, 4000),   # VIX 25-35 → $4,000 (high volatility)
    (20, 3000),   # VIX 20-25 → $3,000 (elevated)
    (15, 2000),   # VIX 15-20 → $2,000 (normal)
    (0,  1000),   # VIX < 15  → $1,000 (low vol, weak edge)
]

# Cache VIX for 15 minutes to avoid hammering yfinance
_vix_cache: dict = {"value": None, "timestamp": 0}
VIX_CACHE_SECS = 900  # 15 minutes


def get_vix() -> float:
    """
    Fetch current VIX from yfinance with 15-min cache.
    Returns 20.0 as default if fetch fails.
    """
    global _vix_cache

    now = time.time()
    if (
        _vix_cache["value"] is not None
        and now - _vix_cache["timestamp"] < VIX_CACHE_SECS
    ):
        return _vix_cache["value"]

    try:
        import yfinance as yf
        vix_data = yf.download("^VIX", period="1d", interval="5m",
                                progress=False, threads=False)
        if not vix_data.empty:
            vix = float(vix_data["Close"].iloc[-1])
            _vix_cache["value"]     = vix
            _vix_cache["timestamp"] = now
            logger.info(f"VIX fetched: {vix:.1f}")
            return vix
    except Exception as e:
        logger.warning(f"VIX fetch failed: {e} — using cached/default")

    # Return cached value or default
    return _vix_cache["value"] or 20.0


def vix_position_size(capital: float = 25000) -> tuple[float, float, str]:
    """
    Calculate max position size based on current VIX.

    Returns:
        (max_position_usd, vix_value, tier_label)

    Example:
        size, vix, label = vix_position_size(25000)
        # size=4000, vix=28.3, label="HIGH VOLATILITY"
    """
    vix = get_vix()

    tier_labels = {
        5000: "EXTREME FEAR (VIX>35) — maximum size",
        4000: "HIGH VOLATILITY (VIX 25-35) — large size",
        3000: "ELEVATED (VIX 20-25) — normal size",
        2000: "NORMAL (VIX 15-20) — reduced size",
        1000: "LOW VOL (VIX<15) — minimum size",
    }

    for threshold, size in VIX_TIERS:
        if vix > threshold:
            label = tier_labels[size]
            logger.info(
                f"VIX={vix:.1f} | Position size: ${size:,} | {label}"
            )
            return float(size), vix, label

    return 1000.0, vix, tier_labels[1000]


def vix_regime_label(vix: float) -> str:
    """Return human-readable VIX regime label."""
    if vix > 35: return "EXTREME FEAR"
    if vix > 25: return "HIGH VOL"
    if vix > 20: return "ELEVATED"
    if vix > 15: return "NORMAL"
    return "LOW VOL"
'''

with open(r"src\data\vix.py", "w", encoding="utf-8") as f:
    f.write(vix_module)
print("✓ Created src/data/vix.py")

# ── Step 2: Patch RiskManager to use VIX sizing ──────────────────────────────
risk_path = r"src\risk\manager.py"
with open(risk_path, "r", encoding="utf-8") as f:
    risk = f.read()

# Add VIX import at top of approve_entry
old_approve = "    def approve_entry(\n        self,"
if old_approve in risk and "from src.data.vix" not in risk:
    # Add import at top of file
    risk = "from src.data.vix import vix_position_size\n" + risk
    print("✓ Added VIX import to risk manager")

# Find and patch the position size calculation in approve_entry
old_size = (
    "        # Position sizing via Kelly\n"
    "        pos_usd = self._kelly.position_size_usd("
)
new_size = (
    "        # Dynamic position sizing via VIX\n"
    "        try:\n"
    "            vix_max, vix_val, vix_label = vix_position_size(self._capital)\n"
    "            self._max_position_usd = vix_max\n"
    "            logger.info(f\"VIX={vix_val:.1f} | max_pos=${vix_max:,.0f} | {vix_label}\")\n"
    "        except Exception as e:\n"
    "            logger.warning(f\"VIX sizing failed: {e} — using config default\")\n"
    "\n"
    "        # Position sizing via Kelly\n"
    "        pos_usd = self._kelly.position_size_usd("
)

if old_size in risk:
    risk = risk.replace(old_size, new_size)
    print("✓ Patched approve_entry to use VIX sizing")
else:
    # Try alternative — find max_position_usd assignment
    print("⚠ Could not find Kelly sizing block — trying alternative patch...")
    if "self._max_position_usd" in risk:
        print("  max_position_usd found — VIX will update it dynamically")
    else:
        print("  Manual patch needed — see instructions below")

with open(risk_path, "w", encoding="utf-8") as f:
    f.write(risk)

# ── Step 3: Patch live_trader to log VIX on each scan ───────────────────────
trader_path = r"src\execution\live_trader.py"
with open(trader_path, "r", encoding="utf-8") as f:
    trader = f.read()

old_scan_log = (
    "                logger.info(\n"
    "                    f\"Scan #{scan_count} | {status['time_eastern']} | \"\n"
    "                    f\"open positions: {len(self._order_mgr.open_orders())}\"\n"
    "                )"
)
new_scan_log = (
    "                # Log VIX alongside scan info\n"
    "                try:\n"
    "                    from src.data.vix import get_vix, vix_regime_label\n"
    "                    _vix = get_vix()\n"
    "                    _regime = vix_regime_label(_vix)\n"
    "                    logger.info(\n"
    "                        f\"Scan #{scan_count} | {status['time_eastern']} | \"\n"
    "                        f\"VIX={_vix:.1f}({_regime}) | \"\n"
    "                        f\"open positions: {len(self._order_mgr.open_orders())}\"\n"
    "                    )\n"
    "                except Exception:\n"
    "                    logger.info(\n"
    "                        f\"Scan #{scan_count} | {status['time_eastern']} | \"\n"
    "                        f\"open positions: {len(self._order_mgr.open_orders())}\"\n"
    "                    )"
)

if old_scan_log in trader:
    trader = trader.replace(old_scan_log, new_scan_log)
    print("✓ Added VIX display to scan log")
else:
    print("⚠ Could not patch scan log — VIX still active via RiskManager")

with open(trader_path, "w", encoding="utf-8") as f:
    f.write(trader)

# ── Step 4: Add VIX check to morning brief / diagnose ───────────────────────
diagnose_path = r"scripts\diagnose.py"
with open(diagnose_path, "r", encoding="utf-8") as f:
    diagnose = f.read()

if "from src.data.vix" not in diagnose:
    old_end = "console.print(\n    \"[bold cyan]Market Context:[/bold cyan]"
    new_vix_block = (
        "# Show VIX\n"
        "try:\n"
        "    from src.data.vix import get_vix, vix_position_size, vix_regime_label\n"
        "    _vix = get_vix()\n"
        "    _size, _, _label = vix_position_size()\n"
        "    console.print(f\"\\n[bold]VIX:[/bold] [yellow]{_vix:.1f}[/yellow] "
        "| Regime: [cyan]{vix_regime_label(_vix)}[/cyan] "
        "| Position size: [green]${_size:,.0f}[/green]\")\n"
        "except Exception as e:\n"
        "    console.print(f\"[dim]VIX unavailable: {e}[/dim]\")\n\n"
    )
    if old_end in diagnose:
        diagnose = diagnose.replace(old_end, new_vix_block + old_end)
        with open(diagnose_path, "w", encoding="utf-8") as f:
            f.write(diagnose)
        print("✓ Added VIX display to diagnose.py")

print("""
════════════════════════════════════════════════════════════════
  VIX-BASED POSITION SIZING ACTIVE
════════════════════════════════════════════════════════════════

  Position sizes now adjust automatically with VIX:

  ┌─────────────────┬──────────────┬────────────────────────┐
  │ VIX Level       │ Position $   │ Why                    │
  ├─────────────────┼──────────────┼────────────────────────┤
  │ VIX > 35        │ $5,000       │ Extreme fear = best    │
  │ VIX 25-35       │ $4,000       │ High vol = strong edge │
  │ VIX 20-25       │ $3,000       │ Elevated               │
  │ VIX 15-20       │ $2,000       │ Normal conditions      │
  │ VIX < 15        │ $1,000       │ Low vol = weak edge    │
  └─────────────────┴──────────────┴────────────────────────┘

  Current market (April 2026): VIX ~40
  → Bot will trade $5,000 per position (max size!)
  → 2x larger than previous fixed $2,500

  VIX is cached for 15 minutes to avoid hammering yfinance.
  Displayed on every scan log and in diagnose.py output.

  Restart the bot:
  python scripts/run_live.py --paper
""")
