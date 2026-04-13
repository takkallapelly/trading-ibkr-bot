"""
Switch to aggressive paper trading mode for faster learning.
Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_aggressive.py

Changes:
  1. config/config.yaml  : relax all signal + risk thresholds
  2. src/strategy/rules.py: lower vol ratio, widen RSI thresholds
  3. src/data/intraday.py : widen intraday RSI thresholds

Goal: generate 5-15 trades per day instead of 0-1
"""

import re

# ── 1. config/config.yaml ────────────────────────────────────────────────────
cfg_path = r"config\config.yaml"
with open(cfg_path, "r", encoding="utf-8") as f:
    cfg = f.read()

changes_cfg = [
    # Signal thresholds — much more relaxed
    ("min_signal_score: 0.60",   "min_signal_score: 0.50"),   # was 0.65, now 0.50
    ("min_signal_score: 0.65",   "min_signal_score: 0.50"),   # catch old value too

    # RSI thresholds — wider to catch more signals
    ("oversold: 10",             "oversold: 20"),              # daily: was 10, now 20
    ("overbought: 90",           "overbought: 80"),            # daily: was 90, now 80

    # Risk — allow more simultaneous positions
    ("max_open_positions: 3",    "max_open_positions: 4"),     # was 3, now 4
    ("max_position_usd: 2500",   "max_position_usd: 3000"),    # was $2500, now $3000

    # Stop/target — tighter stop, same target = more trades, same R:R
    ("stop_loss_atr_mult: 1.5",  "stop_loss_atr_mult: 1.0"),  # tighter stop
    ("take_profit_atr_mult: 3.0","take_profit_atr_mult: 2.0"),# closer target

    # Long only stays true for now
]

applied_cfg = []
for old, new in changes_cfg:
    if old in cfg:
        cfg = cfg.replace(old, new)
        applied_cfg.append(f"  {old} → {new}")

with open(cfg_path, "w", encoding="utf-8") as f:
    f.write(cfg)
print("✓ config.yaml updated:")
for c in applied_cfg:
    print(c)

# ── 2. src/strategy/rules.py ─────────────────────────────────────────────────
rules_path = r"src\strategy\rules.py"
with open(rules_path, "r", encoding="utf-8") as f:
    rules = f.read()

changes_rules = [
    ("min_vol_ratio: float = 0.5,", "min_vol_ratio: float = 0.3,"),   # was 0.5/0.4
    ("min_vol_ratio: float = 0.4,", "min_vol_ratio: float = 0.3,"),   # catch updated
    ("min_atr_pct: float = 0.005,", "min_atr_pct: float = 0.001,"),   # very low ATR ok
    ("min_rr: float = 1.0,",        "min_rr: float = 0.8,"),          # allow R:R >= 0.8
]

applied_rules = []
for old, new in changes_rules:
    if old in rules:
        rules = rules.replace(old, new)
        applied_rules.append(f"  {old} → {new}")

with open(rules_path, "w", encoding="utf-8") as f:
    f.write(rules)
print("\n✓ rules.py updated:")
for c in applied_rules:
    print(c)

# ── 3. src/data/intraday.py — widen 5-min RSI thresholds ────────────────────
intraday_path = r"src\data\intraday.py"
with open(intraday_path, "r", encoding="utf-8") as f:
    intraday = f.read()

changes_intraday = [
    ('"oversold":    15,',   '"oversold":    25,'),   # was 15, now 25 (more signals)
    ('"overbought":  85,',   '"overbought":  75,'),   # was 85, now 75 (more signals)
    ('"min_signal_score":    0.65,', '"min_signal_score":    0.50,'),
    ('"min_signal_score":    0.60,', '"min_signal_score":    0.50,'),
    ('"min_atr_pct":         0.001,','"min_atr_pct":         0.0005,'),
]

applied_intraday = []
for old, new in changes_intraday:
    if old in intraday:
        intraday = intraday.replace(old, new)
        applied_intraday.append(f"  {old} → {new}")

with open(intraday_path, "w", encoding="utf-8") as f:
    f.write(intraday)
print("\n✓ intraday.py updated:")
for c in applied_intraday:
    print(c)

# ── Summary ──────────────────────────────────────────────────────────────────
print("""
════════════════════════════════════════════════════════
  AGGRESSIVE PAPER TRADING MODE ACTIVATED
════════════════════════════════════════════════════════

  What changed:
  ┌─────────────────────────┬──────────┬──────────────┐
  │ Setting                 │ Before   │ After        │
  ├─────────────────────────┼──────────┼──────────────┤
  │ Min signal score        │ 0.65     │ 0.50         │
  │ RSI oversold threshold  │ 15       │ 25           │
  │ RSI overbought          │ 85       │ 75           │
  │ Min volume ratio        │ 0.5      │ 0.3          │
  │ Stop loss ATR mult      │ 1.5x     │ 1.0x         │
  │ Take profit ATR mult    │ 3.0x     │ 2.0x         │
  │ Max open positions      │ 3        │ 4            │
  │ Max position size       │ $2,500   │ $3,000       │
  │ Min R:R ratio           │ 1.0      │ 0.8          │
  └─────────────────────────┴──────────┴──────────────┘

  Expected: 5-15 trades per day vs 0-1 previously
  Risk:     Still paper money — learn fast, adjust later

  Now restart the bot:
  python scripts/run_live.py --paper
""")
