"""
Maximum aggressive paper trading settings.
Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_max_aggressive.py
"""
import re

# ── config.yaml ──────────────────────────────────────────────────────────────
cfg_path = r"config\config.yaml"
with open(cfg_path, "r", encoding="utf-8") as f:
    cfg = f.read()

changes = [
    ("min_signal_score: 0.60", "min_signal_score: 0.45"),
    ("min_signal_score: 0.50", "min_signal_score: 0.45"),
    ("max_open_positions: 3",  "max_open_positions: 7"),   # one per ticker
    ("max_open_positions: 4",  "max_open_positions: 7"),
    ("max_position_usd: 2500", "max_position_usd: 3500"),
    ("max_position_usd: 3000", "max_position_usd: 3500"),
    ("oversold: 10",           "oversold: 30"),
    ("oversold: 20",           "oversold: 30"),
    ("overbought: 90",         "overbought: 70"),
    ("overbought: 80",         "overbought: 70"),
    ("kelly_fraction: 0.5",    "kelly_fraction: 0.75"),
]

for old, new in changes:
    if old in cfg:
        cfg = cfg.replace(old, new)
        print(f"✓ {old} → {new}")

with open(cfg_path, "w", encoding="utf-8") as f:
    f.write(cfg)

# ── rules.py — minimum filters ───────────────────────────────────────────────
rules_path = r"src\strategy\rules.py"
with open(rules_path, "r", encoding="utf-8") as f:
    rules = f.read()

rule_changes = [
    ("min_vol_ratio: float = 0.5,", "min_vol_ratio: float = 0.15,"),
    ("min_vol_ratio: float = 0.4,", "min_vol_ratio: float = 0.15,"),
    ("min_vol_ratio: float = 0.3,", "min_vol_ratio: float = 0.15,"),
    ("min_vol_ratio: float = 0.2,", "min_vol_ratio: float = 0.15,"),
    ("min_atr_pct: float = 0.005,", "min_atr_pct: float = 0.0001,"),
    ("min_atr_pct: float = 0.001,", "min_atr_pct: float = 0.0001,"),
    ("min_rr: float = 1.0,",        "min_rr: float = 0.5,"),
    ("min_rr: float = 0.8,",        "min_rr: float = 0.5,"),
]

for old, new in rule_changes:
    if old in rules:
        rules = rules.replace(old, new)
        print(f"✓ {old} → {new}")

with open(rules_path, "w", encoding="utf-8") as f:
    f.write(rules)

# ── intraday.py — widen RSI thresholds ───────────────────────────────────────
intraday_path = r"src\data\intraday.py"
with open(intraday_path, "r", encoding="utf-8") as f:
    intraday = f.read()

intra_changes = [
    ('"oversold":    15,',   '"oversold":    30,'),
    ('"oversold":    25,',   '"oversold":    30,'),
    ('"overbought":  85,',   '"overbought":  70,'),
    ('"overbought":  75,',   '"overbought":  70,'),
    ('"min_signal_score":    0.65,', '"min_signal_score":    0.45,'),
    ('"min_signal_score":    0.60,', '"min_signal_score":    0.45,'),
    ('"min_signal_score":    0.50,', '"min_signal_score":    0.45,'),
    ('"min_atr_pct":         0.001,', '"min_atr_pct":         0.0001,'),
    ('"min_atr_pct":         0.0005,','"min_atr_pct":         0.0001,'),
]

for old, new in intra_changes:
    if old in intraday:
        intraday = intraday.replace(old, new)
        print(f"✓ {old} → {new}")

with open(intraday_path, "w", encoding="utf-8") as f:
    f.write(intraday)

print("""
✅ Maximum aggressive settings applied:
  min_signal_score : 0.45  (was 0.65)
  RSI oversold     : 30    (was 10)
  RSI overbought   : 70    (was 90)
  min_vol_ratio    : 0.15  (was 0.5)
  max_positions    : 7     (one per ticker)
  min_rr           : 0.5   (was 1.0)
  kelly_fraction   : 0.75  (was 0.5)

Expected: 10-20 signals/day
""")
