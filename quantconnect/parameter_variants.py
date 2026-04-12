"""
quantconnect/parameter_variants.py
────────────────────────────────────
6 pre-built parameter sets to test in QuantConnect.

HOW TO USE:
  1. Copy the VARIANT dict values into rsi_mean_reversion.py at the top
  2. Run backtest in QC Cloud
  3. Compare Sharpe, CAGR, MaxDD across variants
  4. Run: python scripts/sync_qc_params.py --variant <name>
     to apply winning params to your local config.yaml

WHAT EACH VARIANT TESTS:
  baseline     — exact match to current live bot config
  wider_stop   — 1.5×ATR stop (reduces stop-outs, lower win rate needed)
  tighter_signal — raise min_score to 0.75 (fewer but higher-quality trades)
  wider_rsi    — RSI thresholds 15/85 instead of 10/90 (more signals)
  larger_target — 3×ATR target (higher R:R, lower win rate needed)
  conservative — all filters tightened for max precision over frequency
"""

VARIANTS: dict[str, dict] = {

    "baseline": {
        "RSI_OVERSOLD":    10,
        "RSI_OVERBOUGHT":  90,
        "BB_STD":          2.0,
        "MIN_SCORE":       0.60,
        "STOP_ATR_MULT":   1.0,
        "TARGET_ATR_MULT": 2.0,
        "description": "Exact match to current live bot. Use as benchmark.",
    },

    "wider_stop": {
        "RSI_OVERSOLD":    10,
        "RSI_OVERBOUGHT":  90,
        "BB_STD":          2.0,
        "MIN_SCORE":       0.60,
        "STOP_ATR_MULT":   1.5,
        "TARGET_ATR_MULT": 2.0,
        "description": (
            "Wider stop (1.5×ATR). Fixes high stop-out rate. "
            "R:R drops to 1.33 — need 43%+ win rate."
        ),
    },

    "tighter_signal": {
        "RSI_OVERSOLD":    10,
        "RSI_OVERBOUGHT":  90,
        "BB_STD":          2.0,
        "MIN_SCORE":       0.75,
        "STOP_ATR_MULT":   1.0,
        "TARGET_ATR_MULT": 2.0,
        "description": (
            "Raise signal threshold to 0.75 (RSI+BB+EMA all needed). "
            "Fewer trades, higher precision. Test if quality > quantity."
        ),
    },

    "wider_rsi": {
        "RSI_OVERSOLD":    15,
        "RSI_OVERBOUGHT":  85,
        "BB_STD":          2.0,
        "MIN_SCORE":       0.60,
        "STOP_ATR_MULT":   1.0,
        "TARGET_ATR_MULT": 2.0,
        "description": (
            "Relax RSI thresholds from 10/90 to 15/85. "
            "More signals. Tests if 10/90 is leaving money on the table."
        ),
    },

    "larger_target": {
        "RSI_OVERSOLD":    10,
        "RSI_OVERBOUGHT":  90,
        "BB_STD":          2.0,
        "MIN_SCORE":       0.60,
        "STOP_ATR_MULT":   1.0,
        "TARGET_ATR_MULT": 3.0,
        "description": (
            "Raise TP to 3×ATR (R:R = 3.0). "
            "Need only 25%+ win rate. Tests if exits are too early."
        ),
    },

    "conservative": {
        "RSI_OVERSOLD":    8,
        "RSI_OVERBOUGHT":  92,
        "BB_STD":          2.5,
        "MIN_SCORE":       0.80,
        "STOP_ATR_MULT":   1.5,
        "TARGET_ATR_MULT": 2.5,
        "description": (
            "All filters at maximum. Fewest trades, highest precision. "
            "Benchmark for quality-over-frequency approach."
        ),
    },
}


def print_variants() -> None:
    """Print all variants in a readable format."""
    for name, params in VARIANTS.items():
        print(f"\n{'─'*50}")
        print(f"  VARIANT: {name}")
        print(f"  {params['description']}")
        for k, v in params.items():
            if k != "description":
                print(f"    {k:25s} = {v}")


if __name__ == "__main__":
    print_variants()
