"""
Switch BACK to conservative settings for live trading.
Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_conservative.py

Use this when:
  - You have 50+ paper trades accumulated
  - You are ready to go live with real money
  - You want to reduce trade frequency and increase selectivity
"""

# ── 1. config/config.yaml ────────────────────────────────────────────────────
cfg_path = r"config\config.yaml"
with open(cfg_path, "r", encoding="utf-8") as f:
    cfg = f.read()

changes_cfg = [
    ("min_signal_score: 0.50",    "min_signal_score: 0.65"),
    ("oversold: 20",              "oversold: 10"),
    ("overbought: 80",            "overbought: 90"),
    ("max_open_positions: 4",     "max_open_positions: 3"),
    ("max_position_usd: 3000",    "max_position_usd: 2500"),
    ("stop_loss_atr_mult: 1.0",   "stop_loss_atr_mult: 1.5"),
    ("take_profit_atr_mult: 2.0", "take_profit_atr_mult: 3.0"),
]

applied_cfg = []
for old, new in changes_cfg:
    if old in cfg:
        cfg = cfg.replace(old, new)
        applied_cfg.append(f"  {old} → {new}")

with open(cfg_path, "w", encoding="utf-8") as f:
    f.write(cfg)
print("✓ config.yaml restored:")
for c in applied_cfg:
    print(c)

# ── 2. src/strategy/rules.py ─────────────────────────────────────────────────
rules_path = r"src\strategy\rules.py"
with open(rules_path, "r", encoding="utf-8") as f:
    rules = f.read()

changes_rules = [
    ("min_vol_ratio: float = 0.3,", "min_vol_ratio: float = 0.5,"),
    ("min_atr_pct: float = 0.001,", "min_atr_pct: float = 0.005,"),
    ("min_rr: float = 0.8,",        "min_rr: float = 1.0,"),
]

applied_rules = []
for old, new in changes_rules:
    if old in rules:
        rules = rules.replace(old, new)
        applied_rules.append(f"  {old} → {new}")

with open(rules_path, "w", encoding="utf-8") as f:
    f.write(rules)
print("\n✓ rules.py restored:")
for c in applied_rules:
    print(c)

# ── 3. src/data/intraday.py ──────────────────────────────────────────────────
intraday_path = r"src\data\intraday.py"
with open(intraday_path, "r", encoding="utf-8") as f:
    intraday = f.read()

changes_intraday = [
    ('"oversold":    25,',          '"oversold":    15,'),
    ('"overbought":  75,',          '"overbought":  85,'),
    ('"min_signal_score":    0.50,','"min_signal_score":    0.65,'),
    ('"min_atr_pct":         0.0005,','"min_atr_pct":         0.001,'),
]

applied_intraday = []
for old, new in changes_intraday:
    if old in intraday:
        intraday = intraday.replace(old, new)
        applied_intraday.append(f"  {old} → {new}")

with open(intraday_path, "w", encoding="utf-8") as f:
    f.write(intraday)
print("\n✓ intraday.py restored:")
for c in applied_intraday:
    print(c)

print("""
════════════════════════════════════════════════════════
  CONSERVATIVE LIVE TRADING MODE RESTORED
════════════════════════════════════════════════════════

  What restored:
  ┌─────────────────────────┬──────────┬──────────────┐
  │ Setting                 │ Paper    │ Live         │
  ├─────────────────────────┼──────────┼──────────────┤
  │ Min signal score        │ 0.50     │ 0.65         │
  │ RSI oversold threshold  │ 25       │ 10           │
  │ RSI overbought          │ 75       │ 90           │
  │ Min volume ratio        │ 0.3      │ 0.5          │
  │ Stop loss ATR mult      │ 1.0x     │ 1.5x         │
  │ Take profit ATR mult    │ 2.0x     │ 3.0x         │
  │ Max open positions      │ 4        │ 3            │
  │ Max position size       │ $3,000   │ $2,500       │
  │ Min R:R ratio           │ 0.8      │ 1.0          │
  └─────────────────────────┴──────────┴──────────────┘

  Before going live also run:
    python scripts/run_retrain.py        (retrain ML model)
    python scripts/optimize_universe.py  (recheck best tickers)
    python alpha_decay.py                (go-live checklist)

  Then set .env: TRADING_MODE=live
  And run: python scripts/run_live.py --live
""")
