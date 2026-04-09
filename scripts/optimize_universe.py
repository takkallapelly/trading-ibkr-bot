"""
scripts/optimize_universe.py
─────────────────────────────────────────────────────────────────
Automatically finds the best tickers to add to the existing universe.

FIXED tickers (always kept): AVGO, META, MSFT, COST
Goal: Find the best 16 additional tickers to reach 20 total.

Method:
  1. Test each candidate individually
  2. Rank by expectancy
  3. Greedily add tickers that improve portfolio Sharpe
  4. Stop at 20 tickers total

Usage:
    python scripts/optimize_universe.py

Output:
    - Ranked ticker table
    - Optimal 20-ticker universe
    - Final backtest results
    - Offers to update config/config.yaml automatically
"""

import sys
import os
import yaml
from pathlib import Path
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import cfg
from src.backtest.engine import Backtester
from src.strategy.signals import SignalEngine

# ── Fixed tickers — always in the universe ───────────────────────────────────
FIXED_TICKERS = ["AVGO", "META", "MSFT", "COST"]

# ── Candidates to test for the remaining 16 slots ────────────────────────────
CANDIDATES = [
    # Mega-cap tech
    "NVDA", "AAPL", "GOOGL", "AMZN", "TSLA",
    # Financials
    "JPM", "GS", "BAC", "V", "MA",
    # Healthcare
    "UNH", "LLY", "JNJ", "MRK", "ABBV",
    # Consumer
    "NFLX", "HD", "PG", "KO", "PEP",
    # Energy / Industrial
    "XOM", "CVX", "CAT", "HON", "LMT",
    # Tech
    "CRM", "ORCL", "ACN", "NOW", "ADBE",
    # Semiconductors
    "AMD", "QCOM", "MU", "AMAT", "LRCX",
    # ETFs for diversification
    "SPY", "QQQ", "IWM",
]

# Remove any that are already in FIXED_TICKERS
CANDIDATES = [t for t in CANDIDATES if t not in FIXED_TICKERS]
# Remove duplicates
CANDIDATES = list(dict.fromkeys(CANDIDATES))

TARGET_UNIVERSE_SIZE = 20
MAX_NEW_TICKERS      = TARGET_UNIVERSE_SIZE - len(FIXED_TICKERS)  # 16

START_DATE = "2020-01-01"
END_DATE   = "2024-12-31"


def run_backtest(tickers: list[str]) -> dict:
    """Run backtest for a given set of tickers and return metrics."""
    try:
        engine  = Backtester(tickers=tickers, start=START_DATE, end=END_DATE)
        results = engine.run()
        return {
            "tickers":       tickers,
            "sharpe":        results.get("sharpe",       0),
            "cagr":          results.get("cagr",         0),
            "max_dd":        results.get("max_drawdown", 0),
            "win_rate":      results.get("win_rate",     0),
            "expectancy":    results.get("expectancy",   0),
            "total_pnl":     results.get("total_pnl",    0),
            "n_trades":      results.get("n_trades",     0),
            "profit_factor": results.get("profit_factor",0),
        }
    except Exception as e:
        logger.warning(f"Backtest failed for {tickers}: {e}")
        return {
            "tickers":    tickers,
            "sharpe":     -999,
            "cagr":       0,
            "max_dd":     0,
            "win_rate":   0,
            "expectancy": -999,
            "n_trades":   0,
            "error":      str(e),
        }


def rank_candidates(candidates: list[str]) -> list[dict]:
    """
    Test each candidate ticker individually and rank by expectancy.
    This tells us which tickers have the best standalone RSI(2) edge.
    """
    print("\n" + "="*65)
    print(f"STEP 1: Testing {len(candidates)} candidate tickers individually...")
    print("="*65)

    results = []
    for i, ticker in enumerate(candidates, 1):
        print(f"  [{i:2}/{len(candidates)}] {ticker:<6}", end=" ", flush=True)
        r = run_backtest([ticker])
        results.append(r)
        exp    = r.get("expectancy", -999)
        wr     = r.get("win_rate",   0)
        sharpe = r.get("sharpe",     0)
        trades = r.get("n_trades",   0)
        status = "✓" if exp > 0 else "✗"
        print(f"{status} exp=${exp:6.2f}  wr={wr:.0%}  sharpe={sharpe:.2f}  trades={trades}")

    # Sort by expectancy descending
    results.sort(key=lambda x: x.get("expectancy", -999), reverse=True)
    return results


def build_portfolio(ranked: list[dict]) -> list[str]:
    """
    Greedily add tickers to FIXED_TICKERS as long as Sharpe improves.
    Always starts from FIXED_TICKERS as the base.
    """
    print("\n" + "="*65)
    print("STEP 2: Building optimal portfolio...")
    print(f"  Fixed base: {FIXED_TICKERS}")
    print(f"  Max new tickers to add: {MAX_NEW_TICKERS}")
    print("="*65)

    # Get baseline Sharpe with fixed tickers only
    print(f"\n  Baseline (fixed 4): {FIXED_TICKERS}")
    baseline    = run_backtest(FIXED_TICKERS)
    best_sharpe = baseline.get("sharpe", 0)
    best_trades = baseline.get("n_trades", 0)
    print(f"  Baseline Sharpe={best_sharpe:.2f} | trades={best_trades}")

    current_universe = list(FIXED_TICKERS)

    # Only consider tickers with positive expectancy
    good_candidates = [
        r["tickers"][0] for r in ranked
        if r.get("expectancy", -999) > 0
    ]
    print(f"\n  Candidates with positive expectancy: {len(good_candidates)}")

    for candidate in good_candidates:
        if len(current_universe) >= TARGET_UNIVERSE_SIZE:
            print(f"\n  Reached target of {TARGET_UNIVERSE_SIZE} tickers — stopping")
            break

        test_universe = current_universe + [candidate]
        print(f"\n  Testing +{candidate} → {len(test_universe)} tickers...", end=" ", flush=True)

        result     = run_backtest(test_universe)
        new_sharpe = result.get("sharpe", 0)
        new_trades = result.get("n_trades", 0)

        improvement = new_sharpe - best_sharpe
        print(f"Sharpe={new_sharpe:.2f} ({'+' if improvement>=0 else ''}{improvement:.2f}) | trades={new_trades}", end=" ")

        if new_sharpe > best_sharpe:
            current_universe = test_universe
            best_sharpe      = new_sharpe
            print("✅ ADDED")
        else:
            print("❌ SKIPPED")

    return current_universe


def print_ranking_table(ranked: list[dict]) -> None:
    """Print a clean ranking table."""
    print("\n" + "="*65)
    print("CANDIDATE TICKER RANKING")
    print("="*65)
    print(f"{'#':<4} {'Ticker':<8} {'Expectancy':<13} {'Win Rate':<11} {'Sharpe':<9} {'Trades':<8} {'Grade'}")
    print("-"*65)

    for i, r in enumerate(ranked, 1):
        ticker = r["tickers"][0]
        exp    = r.get("expectancy", -999)
        wr     = r.get("win_rate",   0)
        sharpe = r.get("sharpe",     0)
        trades = r.get("n_trades",   0)

        if exp > 30:   grade = "🏆 Excellent"
        elif exp > 15: grade = "✅ Good"
        elif exp > 0:  grade = "⚠️  Marginal"
        else:          grade = "❌ Remove"

        print(f"{i:<4} {ticker:<8} ${exp:<12.2f} {wr:<11.1%} {sharpe:<9.2f} {trades:<8} {grade}")

    # Always show fixed tickers at bottom as reference
    print("-"*65)
    print(f"  Fixed tickers (always included): {', '.join(FIXED_TICKERS)}")


def update_config(new_universe: list[str]) -> None:
    """Update config.yaml with new universe."""
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    config["tickers"]["universe"] = new_universe
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    print(f"\n✅ config.yaml updated!")


def main():
    print("\n" + "🔍 "*20)
    print("UNIVERSE OPTIMIZER — Expanding to 20 Tickers")
    print(f"Fixed:      {FIXED_TICKERS}")
    print(f"Candidates: {len(CANDIDATES)} tickers to evaluate")
    print(f"Target:     {TARGET_UNIVERSE_SIZE} total tickers")
    print("🔍 "*20)

    # Step 1: Rank all candidates individually
    ranked = rank_candidates(CANDIDATES)
    print_ranking_table(ranked)

    good = [r for r in ranked if r.get("expectancy", -999) > 0]
    bad  = [r for r in ranked if r.get("expectancy", -999) <= 0]
    print(f"\n✅ {len(good)} tickers with positive expectancy")
    print(f"❌ {len(bad)} tickers removed (negative expectancy)")

    if not good:
        print("No candidates with positive expectancy found!")
        return

    # Step 2: Build optimal portfolio
    optimal = build_portfolio(ranked)
    new_additions = [t for t in optimal if t not in FIXED_TICKERS]

    # Step 3: Final backtest
    print("\n" + "="*65)
    print("STEP 3: Final backtest with optimal 20-ticker universe...")
    print("="*65)
    final = run_backtest(optimal)

    print(f"""
╔═══════════════════════════════════════════════════════════╗
║           OPTIMAL 20-TICKER UNIVERSE RESULTS             ║
╠═══════════════════════════════════════════════════════════╣
║  Fixed tickers:   {str(FIXED_TICKERS):<40} ║
║  Added tickers:   {str(new_additions):<40} ║
║  Total tickers:   {len(optimal):<40} ║
╠═══════════════════════════════════════════════════════════╣
║  Sharpe ratio:    {final['sharpe']:<40.2f} ║
║  CAGR:            {final['cagr']:<40.2%} ║
║  Max Drawdown:    {final['max_dd']:<40.2%} ║
║  Win Rate:        {final['win_rate']:<40.1%} ║
║  Expectancy:      ${final['expectancy']:<39.2f} ║
║  Total Trades:    {final['n_trades']:<40} ║
║  Profit Factor:   {final['profit_factor']:<40.2f} ║
╚═══════════════════════════════════════════════════════════╝
""")

    print(f"Full universe: {optimal}")

    # Step 4: Ask to update config
    answer = input(
        "\nUpdate config.yaml with this 20-ticker universe? (yes/no): "
    ).strip().lower()

    if answer in ("yes", "y"):
        update_config(optimal)
        print("\n🚀 Done! Restart the bot to trade all 20 tickers:")
        print("   python scripts/run_live.py --paper")
    else:
        print(f"\nNo changes made. To manually update:")
        print(f"  Set tickers.universe in config/config.yaml to:")
        print(f"  {optimal}")


if __name__ == "__main__":
    main()
