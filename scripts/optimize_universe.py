"""
scripts/optimize_universe.py
─────────────────────────────
Automatically tests ticker combinations and finds the optimal universe.

Run this once. It tests every candidate ticker individually, ranks them,
then builds the optimal portfolio by adding tickers one by one as long
as they improve the overall Sharpe ratio.

Usage:
    python scripts/optimize_universe.py

Output:
    - Ranked ticker table
    - Optimal universe recommendation
    - Final backtest results with recommended universe
    - Updates config/config.yaml automatically if you confirm
"""

import sys
import os
import yaml
from itertools import combinations
from pathlib import Path
from loguru import logger

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import cfg
from src.backtest.engine import Backtester
from src.strategy.signals import SignalEngine

# ── Candidate tickers to test ─────────────────────────────────────────────────
# Current universe + candidates to try
CURRENT_UNIVERSE = cfg["tickers"]["universe"]

CANDIDATES = [
    # Current universe
    "META", "MSFT", "AAPL", "NVDA", "NFLX", "COST", "HD", "CRM", "ORCL", "GS",
    # New candidates
    "GOOGL", "AMZN", "TSLA", "JPM", "V", "UNH", "LLY", "AVGO", "MA", "PG",
    "JNJ", "XOM", "BAC", "MRK", "ABBV", "CVX", "KO", "PEP", "TMO", "ACN",
]

# Remove duplicates while preserving order
CANDIDATES = list(dict.fromkeys(CANDIDATES))

START_DATE = "2020-01-01"
END_DATE   = "2024-12-31"


def run_backtest_for_tickers(tickers: list[str]) -> dict:
    """Run backtest for a given set of tickers and return metrics."""
    try:
        engine  = Backtester(
            tickers = tickers,
            start   = START_DATE,
            end     = END_DATE,
        )
        results = engine.run()
        return {
            "tickers":       tickers,
            "n_tickers":     len(tickers),
            "sharpe":        results.get("sharpe", 0),
            "cagr":          results.get("cagr", 0),
            "max_dd":        results.get("max_drawdown", 0),
            "win_rate":      results.get("win_rate", 0),
            "expectancy":    results.get("expectancy", 0),       # ← fixed
            "total_pnl":     results.get("total_pnl", 0),
            "n_trades":      results.get("n_trades", 0),         # ← fixed
            "profit_factor": results.get("profit_factor", 0),
        }
    except Exception as e:
        return {
            "tickers":    tickers,
            "sharpe":     -999,
            "cagr":       0,
            "max_dd":     0,
            "win_rate":   0,
            "expectancy": -999,
            "error":      str(e),
        }


def rank_individual_tickers(candidates: list[str]) -> list[dict]:
    """Test each ticker individually and rank by expectancy."""
    print("\n" + "="*60)
    print("STEP 1: Testing each ticker individually...")
    print("="*60)

    results = []
    for ticker in candidates:
        print(f"  Testing {ticker}...", end=" ", flush=True)
        r = run_backtest_for_tickers([ticker])
        results.append(r)
        exp = r.get("expectancy", -999)
        wr  = r.get("win_rate", 0)
        print(f"Expectancy=${exp:.2f} WR={wr:.1%}")

    # Sort by expectancy descending
    results.sort(key=lambda x: x.get("expectancy", -999), reverse=True)
    return results


def greedy_portfolio_builder(
    ranked_tickers: list[dict],
    max_tickers: int = 12,
) -> list[str]:
    """
    Build optimal portfolio greedily:
    Start with best single ticker, add next ticker only if Sharpe improves.
    """
    print("\n" + "="*60)
    print("STEP 2: Building optimal portfolio (greedy method)...")
    print("="*60)

    # Start with top ticker
    best_tickers   = [ranked_tickers[0]["tickers"][0]]
    best_sharpe    = ranked_tickers[0]["sharpe"]

    print(f"\n  Starting with: {best_tickers} | Sharpe={best_sharpe:.2f}")

    # Remaining candidates in ranked order
    remaining = [r["tickers"][0] for r in ranked_tickers[1:]]

    for candidate in remaining:
        if len(best_tickers) >= max_tickers:
            break

        test_universe = best_tickers + [candidate]
        print(f"\n  Testing adding {candidate} → {test_universe}...", end=" ", flush=True)

        result     = run_backtest_for_tickers(test_universe)
        new_sharpe = result.get("sharpe", 0)

        print(f"Sharpe={new_sharpe:.2f} (was {best_sharpe:.2f})", end=" ")

        if new_sharpe > best_sharpe:
            best_tickers = test_universe
            best_sharpe  = new_sharpe
            print(f"✅ ADDED — Sharpe improved by {new_sharpe - best_sharpe + (new_sharpe - best_sharpe):.2f}")
        else:
            print(f"❌ SKIPPED — no improvement")

    return best_tickers


def print_ticker_ranking(ranked: list[dict]) -> None:
    """Print ranked ticker table."""
    print("\n" + "="*60)
    print("TICKER RANKING (individual performance)")
    print("="*60)
    print(f"{'Rank':<5} {'Ticker':<8} {'WinRate':<10} {'Expectancy':<12} {'Sharpe':<8} {'Verdict'}")
    print("-"*60)

    for i, r in enumerate(ranked, 1):
        ticker = r["tickers"][0]
        wr     = r.get("win_rate", 0)
        exp    = r.get("expectancy", -999)
        sharpe = r.get("sharpe", 0)

        if exp > 25:
            verdict = "🌟 Excellent"
        elif exp > 10:
            verdict = "✅ Good"
        elif exp > 0:
            verdict = "⚠️  Marginal"
        else:
            verdict = "❌ Remove"

        print(f"{i:<5} {ticker:<8} {wr:<10.1%} ${exp:<11.2f} {sharpe:<8.2f} {verdict}")


def update_config(new_universe: list[str]) -> None:
    """Update config.yaml with new universe."""
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    config["tickers"]["universe"] = new_universe

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    print(f"\n✅ config.yaml updated with new universe: {new_universe}")


def main():
    print("\n" + "🔍 "*20)
    print("UNIVERSE OPTIMIZER")
    print("Testing all candidate tickers to find the best combination")
    print("🔍 "*20)

    # Step 1: Rank individual tickers
    ranked = rank_individual_tickers(CANDIDATES)
    print_ticker_ranking(ranked)

    # Filter out clearly bad tickers (negative expectancy)
    good_tickers = [r for r in ranked if r.get("expectancy", -999) > 0]
    print(f"\n{len(good_tickers)} tickers with positive expectancy "
          f"(removed {len(ranked) - len(good_tickers)} negative)")

    if not good_tickers:
        print("❌ No tickers with positive expectancy found!")
        return

    # Step 2: Build optimal portfolio greedily
    optimal_universe = greedy_portfolio_builder(good_tickers)

    # Step 3: Final backtest with optimal universe
    print("\n" + "="*60)
    print("STEP 3: Final backtest with optimal universe...")
    print("="*60)
    print(f"Optimal universe: {optimal_universe}")

    final = run_backtest_for_tickers(optimal_universe)

    print(f"""
╔══════════════════════════════════════════╗
║     OPTIMAL UNIVERSE RESULTS             ║
╠══════════════════════════════════════════╣
║  Tickers:    {str(optimal_universe):<28} ║
║  Sharpe:     {final['sharpe']:<28.2f} ║
║  CAGR:       {final['cagr']:<28.2%} ║
║  Max DD:     {final['max_dd']:<28.2%} ║
║  Win Rate:   {final['win_rate']:<28.1%} ║
║  Expectancy: ${final['expectancy']:<27.2f} ║
║  Trades:     {final['n_trades']:<28} ║
╚══════════════════════════════════════════╝
""")

    # Step 4: Ask to update config
    answer = input(
        f"\nUpdate config.yaml with this universe? (yes/no): "
    ).strip().lower()

    if answer in ("yes", "y"):
        update_config(optimal_universe)
        print("\n🚀 Done! Run python scripts/run_backtest.py to verify.")
    else:
        print(f"\nNo changes made. To manually update, set universe to:")
        print(f"{optimal_universe}")


if __name__ == "__main__":
    main()
