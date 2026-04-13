"""
Fix signal thresholds to capture near-miss trades.
Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_thresholds.py

Changes:
  1. config/config.yaml  : min_signal_score 0.65 -> 0.60
  2. src/strategy/rules.py: min_vol_ratio 0.5 -> 0.4
"""
import sys

# ── Fix 1: config.yaml ───────────────────────────────────────────────────────
cfg_path = r"config\config.yaml"
try:
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = f.read()
    old = "  min_signal_score: 0.65"
    new = "  min_signal_score: 0.60"
    if old in cfg:
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(cfg.replace(old, new))
        print("✓ config.yaml: min_signal_score 0.65 → 0.60")
    else:
        print("⚠ config.yaml: pattern not found — checking current value:")
        for line in cfg.split('\n'):
            if 'min_signal_score' in line:
                print(f"  {line.strip()}")
except Exception as e:
    print(f"✗ config.yaml failed: {e}")
    sys.exit(1)

# ── Fix 2: rules.py ──────────────────────────────────────────────────────────
rules_path = r"src\strategy\rules.py"
try:
    with open(rules_path, "r", encoding="utf-8") as f:
        rules = f.read()
    old = "min_vol_ratio: float = 0.5,"
    new = "min_vol_ratio: float = 0.4,"
    if old in rules:
        with open(rules_path, "w", encoding="utf-8") as f:
            f.write(rules.replace(old, new))
        print("✓ rules.py: min_vol_ratio 0.5 → 0.4")
    else:
        print("⚠ rules.py: pattern not found — checking current value:")
        for line in rules.split('\n'):
            if 'min_vol_ratio' in line:
                print(f"  {line.strip()}")
except Exception as e:
    print(f"✗ rules.py failed: {e}")
    sys.exit(1)

print()
print("All fixes applied! Now restart the bot:")
print("  python scripts/run_live.py --paper")
print()
print("With RSI at 0-2 on AVGO/META/MSFT, trades should fire on next scan.")
