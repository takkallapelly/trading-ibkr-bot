"""
src/diagnostics/trade_analyzer.py
──────────────────────────────────
Pure analytics on the trade log. No I/O — takes a DataFrame, returns DataFrames.

Answers the key diagnostic questions:
  - Which tickers are dragging down performance?
  - Which hours of day does the strategy work best?
  - Do high-score signals actually outperform low-score signals?
  - Is the stop loss too tight (high stop-out rate)?
  - Are there regime patterns (Monday bad, Friday good)?
"""
from __future__ import annotations

import pandas as pd
import numpy as np


class TradeAnalyzer:
    """
    Slice and dice a closed-trade DataFrame to find where edge is leaking.

    Args:
        trades: DataFrame with columns: ticker, entry_time, exit_time, pnl,
                signal_score, exit_reason, entry_price, stop_price, target_price, qty
    """

    def __init__(self, trades: pd.DataFrame):
        self._raw = trades.copy()
        if not trades.empty:
            self._df = self._prepare(trades)

    # ── Public API ────────────────────────────────────────────────────────────

    def overall_stats(self) -> dict:
        """Overall performance summary."""
        if self._raw.empty:
            raise ValueError("No closed trades to analyze.")
        df = self._df
        wins   = df[df["pnl"] > 0]["pnl"]
        losses = df[df["pnl"] <= 0]["pnl"]
        total  = len(df)
        return {
            "n_trades":      total,
            "win_rate":      len(wins) / total if total else 0.0,
            "total_pnl":     float(df["pnl"].sum()),
            "expectancy":    float(df["pnl"].mean()),
            "profit_factor": float(wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf"),
            "avg_win":       float(wins.mean()) if len(wins) else 0.0,
            "avg_loss":      float(losses.mean()) if len(losses) else 0.0,
            "best_trade":    float(df["pnl"].max()),
            "worst_trade":   float(df["pnl"].min()),
            "stop_out_rate": float((df["exit_reason"] == "STOP_LOSS").mean()) if "exit_reason" in df.columns else 0.0,
        }

    def by_ticker(self) -> pd.DataFrame:
        """Win rate and P&L broken down by ticker."""
        return self._slice_by("ticker")

    def by_hour(self) -> pd.DataFrame:
        """Win rate by entry hour (9=9am, 10=10am, ...). Reveals time-of-day edge."""
        return self._slice_by("entry_hour")

    def by_day_of_week(self) -> pd.DataFrame:
        """Win rate by day of week (0=Monday ... 4=Friday)."""
        return self._slice_by("entry_dow")

    def by_score_bucket(self) -> pd.DataFrame:
        """Win rate by signal score bucket (0.6-0.7, 0.7-0.8, 0.8-1.0).
        If high-score trades don't outperform, the scoring model needs work."""
        return self._slice_by("score_bucket")

    def by_exit_reason(self) -> pd.DataFrame:
        """Count and avg P&L by exit reason (TAKE_PROFIT, STOP_LOSS, EOD, MANUAL)."""
        if "exit_reason" not in self._df.columns:
            return pd.DataFrame()
        g = self._df.groupby("exit_reason")["pnl"]
        return pd.DataFrame({
            "n_trades":  g.count(),
            "total_pnl": g.sum(),
            "avg_pnl":   g.mean(),
        })

    def consecutive_loss_analysis(self) -> dict:
        """Analyse loss streaks — are they clustered (regime) or random?"""
        df = self._df.sort_values("entry_time")
        streaks = []
        current = 0
        for pnl in df["pnl"]:
            if pnl <= 0:
                current += 1
                streaks.append(current)
            else:
                current = 0
                streaks.append(0)
        return {
            "max_streak":    int(max(streaks)) if streaks else 0,
            "avg_streak":    float(np.mean([s for s in streaks if s > 0])) if any(streaks) else 0.0,
            "streak_counts": pd.Series(streaks).value_counts().to_dict(),
        }

    def edge_leakage_report(self) -> list[tuple[str, str]]:
        """
        Return a list of (finding, severity) tuples identifying where
        the strategy is leaking edge.

        Severity: HIGH = fix immediately, MEDIUM = investigate, LOW = monitor
        """
        findings = []
        stats = self.overall_stats()

        # Win rate below break-even for 1:2 R:R
        if stats["win_rate"] < 0.40:
            findings.append((
                f"Win rate {stats['win_rate']:.1%} is critically low — "
                "strategy has negative expectancy at any R:R below 2.5",
                "HIGH"
            ))
        elif stats["win_rate"] < 0.48:
            findings.append((
                f"Win rate {stats['win_rate']:.1%} is marginal — "
                "need R:R > 2.0 to be profitable",
                "MEDIUM"
            ))

        # Stop-out rate
        if stats["stop_out_rate"] > 0.65:
            findings.append((
                f"Stop-out rate {stats['stop_out_rate']:.1%} is high — "
                "stops may be too tight (1×ATR). Consider 1.5×ATR.",
                "HIGH"
            ))

        # Ticker drag
        by_t = self.by_ticker()
        losers = by_t[by_t["total_pnl"] < 0]
        if len(losers) > 0:
            names = ", ".join(losers.index.tolist())
            findings.append((
                f"Tickers destroying edge: {names} — "
                "consider removing or tightening signal threshold for these",
                "MEDIUM" if len(losers) <= 2 else "HIGH"
            ))

        # Score bucket validation
        by_s = self.by_score_bucket()
        if len(by_s) >= 2:
            low_bucket  = by_s[by_s.index.str.startswith("0.6")]["win_rate"].values
            high_bucket = by_s[by_s.index.str.startswith("0.8")]["win_rate"].values
            if len(low_bucket) and len(high_bucket):
                if high_bucket[0] <= low_bucket[0]:
                    findings.append((
                        "High-score signals (0.8+) are NOT outperforming low-score (0.6-0.7) — "
                        "signal scoring model needs recalibration",
                        "HIGH"
                    ))

        # Hour of day
        by_h = self.by_hour()
        if not by_h.empty:
            worst_hour = by_h["total_pnl"].idxmin()
            worst_wr   = by_h.loc[worst_hour, "win_rate"]
            if worst_wr < 0.35:
                findings.append((
                    f"Hour {worst_hour}:00 has win rate {worst_wr:.1%} — "
                    "consider blocking this hour in config.yaml",
                    "MEDIUM"
                ))

        if not findings:
            findings.append(("No critical leakage found — strategy is performing within expected bounds", "LOW"))

        return findings

    # ── Private ───────────────────────────────────────────────────────────────

    def _prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["entry_time"] = pd.to_datetime(out["entry_time"])
        out["entry_hour"] = out["entry_time"].dt.hour
        out["entry_dow"]  = out["entry_time"].dt.dayofweek  # 0=Mon
        if "signal_score" in out.columns:
            out["score_bucket"] = pd.cut(
                out["signal_score"].clip(0.6, 1.0),
                bins=[0.6, 0.7, 0.8, 1.01],
                labels=["0.6-0.7", "0.7-0.8", "0.8-1.0"],
                right=False,
            )
        return out

    def _slice_by(self, col: str) -> pd.DataFrame:
        df = self._df
        if df.empty or col not in df.columns:
            return pd.DataFrame()
        g = df.groupby(col, observed=True)
        wins   = df[df["pnl"] > 0].groupby(col, observed=True).size()
        totals = g.size()
        result = pd.DataFrame({
            "n_trades":  totals,
            "win_rate":  (wins / totals).fillna(0).round(3),
            "total_pnl": g["pnl"].sum().round(2),
            "avg_pnl":   g["pnl"].mean().round(2),
        })
        return result.sort_values("total_pnl", ascending=False)
