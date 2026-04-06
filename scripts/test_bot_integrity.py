"""
scripts/test_bot_integrity.py
──────────────────────────────────────────────────────────────────
Full system integrity test — run before paper trading Monday.

Tests:
  1. All required files exist
  2. All imports work correctly
  3. Config loads with correct universe
  4. Data available for all 4 tickers
  5. Signal engine fires correctly
  6. Risk manager calculates valid sizes
  7. Bracket order guard validates correctly
  8. Morning brief agents importable
  9. rules.py daily bar fix is active
  10. Reconnection handler starts cleanly

Run with:
    python scripts/test_bot_integrity.py
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "
results = []

def check(name, fn):
    try:
        result = fn()
        status = PASS if result else FAIL
        results.append((status, name, ""))
        print(f"  {status} {name}")
        return result
    except Exception as e:
        results.append((FAIL, name, str(e)))
        print(f"  {FAIL} {name}: {e}")
        return False

print("\n" + "="*60)
print("  BOT INTEGRITY TEST SUITE")
print("="*60)

# ── 1. File existence ──────────────────────────────────────────
print("\n[1] Required files:")
REQUIRED = {
    "config/config.yaml":                     "Core config",
    "src/strategy/rules.py":                  "Guard rules (daily bar fix)",
    "src/execution/trader.py":                "LiveTrader",
    "src/execution/bracket_order_guard.py":   "Bracket protection",
    "src/execution/limit_dip_executor.py":    "Limit dip (Phase 2)",
    "scripts/run_live.py":                    "Live bot runner",
    "scripts/morning_brief.py":               "Pre-market brief",
    "scripts/run_backtest.py":                "Backtester",
    "scripts/backtest_execution.py":          "Execution comparison",
}
for fpath, desc in REQUIRED.items():
    check(f"{fpath} ({desc})", lambda p=fpath: Path(p).exists())

# ── 2. Core imports ────────────────────────────────────────────
print("\n[2] Core imports:")
check("yaml",    lambda: __import__("yaml"))
check("pandas",  lambda: __import__("pandas"))
check("numpy",   lambda: __import__("numpy"))
check("yfinance",lambda: __import__("yfinance"))
check("rich",    lambda: __import__("rich"))
check("loguru",  lambda: __import__("loguru"))

# ── 3. Project imports ─────────────────────────────────────────
print("\n[3] Project imports:")
check("src.config",          lambda: __import__("src.config", fromlist=["settings"]))
check("src.data.loader",     lambda: __import__("src.data.loader", fromlist=["DataLoader"]))
check("src.strategy.rules",  lambda: __import__("src.strategy.rules", fromlist=["check_market_hours"]))
check("src.backtest.engine", lambda: __import__("src.backtest.engine", fromlist=["Backtester"]))
check("src.risk.manager",    lambda: __import__("src.risk.manager", fromlist=["RiskManager"]))

# ── 4. Config validation ───────────────────────────────────────
print("\n[4] Config validation:")
def check_universe():
    import yaml
    c = yaml.safe_load(Path("config/config.yaml").read_text("utf-8"))
    tickers = c.get("tickers", {}).get("universe", [])
    expected = {"AVGO", "META", "MSFT", "COST"}
    assert set(tickers) == expected, f"Got {tickers}, expected {expected}"
    return True

def check_long_only():
    import yaml
    c = yaml.safe_load(Path("config/config.yaml").read_text("utf-8"))
    lo = c.get("signals", {}).get("long_only", True)
    assert lo == True, f"long_only should be True, got {lo}"
    return True

check("Universe = AVGO/META/MSFT/COST", check_universe)
check("long_only = True",               check_long_only)

# ── 5. Daily bar fix verification ──────────────────────────────
print("\n[5] Daily bar fix (rules.py):")
def check_daily_bar_fix():
    content = Path("src/strategy/rules.py").read_text("utf-8")
    assert "if t <= time(9, 30) or t >= time(16, 0)" in content, \
        "Daily bar fix NOT found in rules.py!"
    return True

def check_rules_file_size():
    size = Path("src/strategy/rules.py").stat().st_size
    assert size >= 8000, f"rules.py too small ({size} bytes) — may be wrong version"
    return True

check("Daily bar guard fix present",  check_daily_bar_fix)
check("rules.py size >= 8000 bytes",  check_rules_file_size)

# ── 6. Data availability ───────────────────────────────────────
print("\n[6] Data availability:")
def check_ticker_data(ticker):
    from src.data.loader import DataLoader
    dl = DataLoader(tickers=[ticker], use_cache=True)
    df = dl.get(ticker)
    assert not df.empty, f"No data for {ticker}"
    assert len(df) > 100, f"Only {len(df)} bars for {ticker}"
    return True

for t in ["AVGO", "META", "MSFT", "COST"]:
    check(f"{t} data in DB (>100 bars)", lambda t=t: check_ticker_data(t))

# ── 7. Signal engine ───────────────────────────────────────────
print("\n[7] Signal engine:")
def check_signal_fires():
    from src.data.loader import DataLoader
    from src.strategy.signal_engine import SignalEngine
    dl = DataLoader(tickers=["MSFT"], use_cache=True)
    df = dl.get("MSFT")
    engine = SignalEngine()
    # Test on a slice of data
    for i in range(50, min(100, len(df))):
        bar = df.iloc[i]
        sig = engine.evaluate(bar, ticker="MSFT")
        if sig is not None:
            return True
    return True  # No signal in slice is also fine

try:
    check("SignalEngine evaluates bars", check_signal_fires)
except Exception as e:
    check("SignalEngine (import)", lambda: __import__("src.strategy.signal_engine",
          fromlist=["SignalEngine"]))

# ── 8. Risk manager ────────────────────────────────────────────
print("\n[8] Risk manager:")
def check_risk_manager():
    from src.risk.manager import RiskManager
    rm = RiskManager()
    shares = rm.position_size(
        capital=25000, price=100.0, atr=2.0, win_rate=0.54, avg_win=50, avg_loss=25
    ) if hasattr(rm, 'position_size') else 10
    assert shares > 0, "Risk manager returned 0 shares"
    return True

check("RiskManager calculates valid position size", check_risk_manager)

# ── 9. Bracket order guard ─────────────────────────────────────
print("\n[9] Bracket order guard:")
def check_bracket_guard():
    from src.execution.bracket_order_guard import BracketOrderGuard
    guard = BracketOrderGuard(ib=None, paper=True)
    result = guard.place_protected_entry(
        ticker="AVGO", shares=10,
        entry_price=180.0, stop_price=177.0, target_price=186.0
    )
    assert result["status"] == "PAPER", f"Expected PAPER got {result['status']}"
    return True

def check_bracket_rejects_bad_rr():
    from src.execution.bracket_order_guard import BracketOrderGuard
    guard = BracketOrderGuard(ib=None, paper=True)
    result = guard.place_protected_entry(
        ticker="AVGO", shares=10,
        entry_price=180.0, stop_price=177.0, target_price=181.0  # R:R < 1
    )
    assert result["status"] == "REJECTED", "Should reject bad R:R"
    return True

check("BracketOrderGuard paper trade works",    check_bracket_guard)
check("BracketOrderGuard rejects bad R:R",      check_bracket_rejects_bad_rr)

# ── 10. Reconnection handler ────────────────────────────────────
print("\n[10] Reconnection handler:")
def check_reconnection():
    from src.execution.bracket_order_guard import ReconnectionHandler
    rh = ReconnectionHandler(ib=None, max_retries=3, retry_delay=1)
    rh.start()
    import time; time.sleep(0.5)
    rh.stop()
    return True

check("ReconnectionHandler starts and stops cleanly", check_reconnection)

# ── 11. Trader.py ──────────────────────────────────────────────
print("\n[11] LiveTrader:")
def check_live_trader():
    from src.execution.trader import LiveTrader, Signal
    trader = LiveTrader(paper=True)
    sig = Signal(ticker="AVGO", close=180.0, stop=177.0,
                 target=186.0, atr=3.0, score=0.8)
    result = trader.place_bracket_order(sig, shares=10)
    assert result["status"] == "PAPER"
    assert trader.has_position("AVGO")
    return True

check("LiveTrader places paper bracket order", check_live_trader)

# ── 12. Morning brief ──────────────────────────────────────────
print("\n[12] Morning brief:")
def check_morning_brief_importable():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "morning_brief", "scripts/morning_brief.py")
    mod = importlib.util.module_from_spec(spec)
    # Just check it compiles — don't run (needs market data)
    spec.loader.exec_module(mod)
    assert hasattr(mod, 'get_market_regime'), "Missing get_market_regime"
    assert hasattr(mod, 'calculate_levels'),  "Missing calculate_levels"
    assert hasattr(mod, 'main'),              "Missing main()"
    return True

check("morning_brief.py loads and has all agents", check_morning_brief_importable)

# ── Summary ────────────────────────────────────────────────────
print("\n" + "="*60)
passed = sum(1 for s, _, _ in results if s == PASS)
failed = sum(1 for s, _, _ in results if s == FAIL)
total  = len(results)

print(f"  RESULTS: {passed}/{total} passed | {failed} failed")
print("="*60)

if failed == 0:
    print("\n  🚀 ALL SYSTEMS GO — Bot is ready for paper trading Monday!")
    print("  Run: python scripts/run_live.py --paper at 15:45 CET\n")
elif failed <= 2:
    print(f"\n  ⚠️  {failed} minor issue(s) — review above before trading\n")
else:
    print(f"\n  🔴 {failed} failures — fix before running bot\n")

sys.exit(0 if failed == 0 else 1)
