"""
src/dashboard/journal.py
─────────────────────────
Trade Journal — inspired by Mark Douglas, Trading in the Zone.

Douglas's core insight: consistent traders think like casinos.
They evaluate performance over SAMPLES of trades (20-30), not
trade by trade. They never deviate from the system.

This journal tracks:
  1. Every trade with its outcome
  2. Whether any trade was manually overridden
  3. Sample-based performance (every 20 trades)
  4. The "Casino Exercise" — 20 consecutive trades, no overrides

"The casino doesn't get emotional about any individual hand.
 It trusts the edge over the sample." — Mark Douglas

Usage:
    journal = TradeJournal()
    journal.record_trade(trade_dict)
    sample = journal.current_sample_stats()
    print(journal.casino_exercise_status())
"""

from __future__ import annotations

import json
from datetime import datetime, date
from pathlib import Path
from dataclasses import dataclass, field, asdict

from loguru import logger
from src.config import settings


JOURNAL_PATH = settings.DATA_DIR / "trade_journal.json"
SAMPLE_SIZE  = 20   # Douglas recommends 20-trade samples


@dataclass
class JournalEntry:
    """One trade entry in the journal."""
    # Trade details
    id            : int
    ticker        : str
    side          : str
    entry_price   : float
    exit_price    : float | None
    pnl           : float | None
    exit_reason   : str | None
    signal_score  : float
    entry_time    : str
    exit_time     : str | None

    # Douglas psychology tracking
    was_overridden   : bool  = False   # did you manually close/skip?
    override_reason  : str   = ""      # why did you deviate?
    followed_rules   : bool  = True    # did you follow ALL rules?
    emotional_state  : str   = ""      # calm / anxious / excited / fearful

    # Sample tracking
    sample_number    : int   = 1       # which 20-trade sample is this?
    trade_in_sample  : int   = 1       # which trade within the sample?

    # Notes
    notes            : str   = ""


class TradeJournal:
    """
    Douglas-inspired trade journal with sample-based analysis.

    Key philosophy:
      - Evaluate performance in samples of 20 trades
      - Never deviate from the system
      - Track emotional state at each trade
      - The edge plays out over samples, not individual trades
    """

    def __init__(self, journal_path: Path | None = None):
        self.path    = journal_path or JOURNAL_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: list[JournalEntry] = []
        self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def record_trade(
        self,
        trade: dict,
        emotional_state: str = "calm",
        notes: str = "",
        followed_rules: bool = True,
        was_overridden: bool = False,
        override_reason: str = "",
    ) -> JournalEntry:
        """
        Record a completed trade in the journal.

        Args:
            trade          : trade dict from database
            emotional_state: calm / anxious / excited / fearful
            notes          : free text notes
            followed_rules : did you follow all system rules?
            was_overridden : did you manually override the bot?
            override_reason: why did you deviate?
        """
        n       = len(self._entries) + 1
        sample  = ((n - 1) // SAMPLE_SIZE) + 1
        in_samp = ((n - 1) % SAMPLE_SIZE) + 1

        entry = JournalEntry(
            id           = n,
            ticker       = trade.get("ticker", ""),
            side         = trade.get("side", "LONG"),
            entry_price  = float(trade.get("entry_price", 0)),
            exit_price   = trade.get("exit_price"),
            pnl          = trade.get("pnl"),
            exit_reason  = trade.get("exit_reason"),
            signal_score = float(trade.get("signal_score", 0)),
            entry_time   = str(trade.get("entry_time", "")),
            exit_time    = str(trade.get("exit_time", "")),
            was_overridden  = was_overridden,
            override_reason = override_reason,
            followed_rules  = followed_rules,
            emotional_state = emotional_state,
            sample_number   = sample,
            trade_in_sample = in_samp,
            notes           = notes,
        )

        self._entries.append(entry)
        self._save()

        if was_overridden:
            logger.warning(
                f"Journal: Trade {n} OVERRIDDEN | {entry.ticker} | "
                f"reason: {override_reason}"
            )
        else:
            logger.info(
                f"Journal: Trade {n} recorded | {entry.ticker} | "
                f"pnl=${entry.pnl:.2f} | sample {sample}/trade {in_samp}"
            )

        # Check if sample is complete
        if in_samp == SAMPLE_SIZE:
            stats = self.sample_stats(sample)
            logger.success(
                f"Sample {sample} complete! | "
                f"Win rate: {stats['win_rate']:.1%} | "
                f"P&L: ${stats['total_pnl']:,.2f} | "
                f"Overrides: {stats['overrides']}"
            )

        return entry

    def current_sample_stats(self) -> dict:
        """Stats for the current in-progress sample."""
        if not self._entries:
            return self._empty_sample_stats(1)
        current_sample = self._entries[-1].sample_number
        return self.sample_stats(current_sample)

    def sample_stats(self, sample_number: int) -> dict:
        """
        Douglas-style sample analysis for a specific 20-trade block.

        This is how the casino thinks:
        - Did the edge play out over the sample?
        - How many overrides (deviations from the system)?
        - Is the win rate consistent with backtest expectations?
        """
        entries = [e for e in self._entries if e.sample_number == sample_number]
        if not entries:
            return self._empty_sample_stats(sample_number)

        closed  = [e for e in entries if e.pnl is not None]
        wins    = [e for e in closed if e.pnl > 0]
        losses  = [e for e in closed if e.pnl < 0]
        overrides = [e for e in entries if e.was_overridden]

        total_pnl = sum(e.pnl for e in closed)
        win_rate  = len(wins) / len(closed) if closed else 0.0
        avg_win   = sum(e.pnl for e in wins)   / len(wins)   if wins   else 0.0
        avg_loss  = sum(e.pnl for e in losses) / len(losses) if losses else 0.0

        # Douglas edge assessment
        edge_working = win_rate >= 0.40 and total_pnl > 0
        system_discipline = len(overrides) == 0

        return {
            "sample_number":   sample_number,
            "total_trades":    len(entries),
            "closed_trades":   len(closed),
            "wins":            len(wins),
            "losses":          len(losses),
            "win_rate":        win_rate,
            "total_pnl":       round(total_pnl, 2),
            "avg_win":         round(avg_win, 2),
            "avg_loss":        round(avg_loss, 2),
            "overrides":       len(overrides),
            "edge_working":    edge_working,
            "system_discipline": system_discipline,
            "douglas_verdict": self._douglas_verdict(
                win_rate, total_pnl, len(overrides)
            ),
        }

    def all_sample_stats(self) -> list[dict]:
        """Stats for every completed sample."""
        if not self._entries:
            return []
        max_sample = max(e.sample_number for e in self._entries)
        return [self.sample_stats(i) for i in range(1, max_sample + 1)]

    def casino_exercise_status(self) -> dict:
        """
        Douglas's Casino Exercise status.

        The exercise: take 20 consecutive signals, never override,
        never skip, never move stops. Record every outcome.
        Evaluate the SAMPLE, not the individual trades.

        Returns the current status of the exercise.
        """
        stats    = self.current_sample_stats()
        n        = len(self._entries)
        in_samp  = stats["total_trades"]
        complete = in_samp >= SAMPLE_SIZE

        return {
            "exercise_active":    True,
            "trades_completed":   n,
            "current_sample":     stats["sample_number"],
            "trades_in_sample":   in_samp,
            "trades_remaining":   max(0, SAMPLE_SIZE - in_samp),
            "sample_complete":    complete,
            "current_win_rate":   stats["win_rate"],
            "current_pnl":        stats["total_pnl"],
            "overrides_this_sample": stats["overrides"],
            "discipline_intact":  stats["overrides"] == 0,
            "douglas_message":    self._douglas_message(
                in_samp, stats["overrides"], stats["win_rate"]
            ),
        }

    def override_alert(self, ticker: str, reason: str) -> None:
        """
        Call this if you MANUALLY close or skip a trade.
        Douglas says: every override is a breach of discipline.
        """
        logger.warning(
            f"⚠️  SYSTEM OVERRIDE DETECTED | {ticker} | {reason}\n"
            f"    Douglas says: 'Every time you deviate from your rules,\n"
            f"    you undermine your belief in your ability to be consistent.'"
        )

    def total_stats(self) -> dict:
        """Overall stats across all samples."""
        if not self._entries:
            return {}

        closed    = [e for e in self._entries if e.pnl is not None]
        wins      = [e for e in closed if e.pnl > 0]
        overrides = [e for e in self._entries if e.was_overridden]

        return {
            "total_trades":   len(self._entries),
            "closed_trades":  len(closed),
            "win_rate":       len(wins) / len(closed) if closed else 0.0,
            "total_pnl":      round(sum(e.pnl for e in closed), 2),
            "total_overrides":len(overrides),
            "override_rate":  len(overrides) / len(self._entries) if self._entries else 0.0,
            "samples_completed": max((e.sample_number for e in self._entries), default=0),
            "discipline_score": 1.0 - (len(overrides) / max(len(self._entries), 1)),
        }

    # ── Private ───────────────────────────────────────────────────────────────

    def _douglas_verdict(
        self,
        win_rate: float,
        total_pnl: float,
        overrides: int,
    ) -> str:
        """Douglas-style verdict on the sample."""
        if overrides > 0:
            return (
                f"⚠️  {overrides} override(s) detected. "
                "The casino never deviates from its rules. "
                "Discipline is the foundation of consistency."
            )
        if win_rate >= 0.45 and total_pnl > 0:
            return (
                "✅ Edge working. Win rate and P&L confirm the edge "
                "is playing out over this sample. Stay the course."
            )
        if win_rate >= 0.40 and total_pnl > 0:
            return (
                "✅ Edge marginally working. P&L positive. "
                "Continue without modification — the sample may improve."
            )
        if total_pnl < 0 and overrides == 0:
            return (
                "⚠️  Losing sample but no overrides — good discipline. "
                "Remember: even casinos have losing sessions. "
                "Evaluate after 3 samples before making any changes."
            )
        return (
            "📊 Sample in progress. "
            "Douglas: 'Don't evaluate the edge on incomplete samples.'"
        )

    def _douglas_message(
        self,
        trades_in_sample: int,
        overrides: int,
        win_rate: float,
    ) -> str:
        if trades_in_sample == 0:
            return "Start taking signals. The casino exercise begins with the first trade."
        if overrides > 0:
            return f"⚠️ {overrides} override(s) this sample. Restart the exercise to get clean data."
        if trades_in_sample < 5:
            return "Early in the sample. Don't evaluate the edge yet — too few trades."
        if trades_in_sample < SAMPLE_SIZE:
            remaining = SAMPLE_SIZE - trades_in_sample
            return (
                f"{remaining} trades remaining in this sample. "
                "Don't change anything until the sample is complete."
            )
        return (
            f"Sample complete! Win rate: {win_rate:.1%}. "
            "Now evaluate and start the next sample."
        )

    def _empty_sample_stats(self, n: int) -> dict:
        return {
            "sample_number": n, "total_trades": 0, "closed_trades": 0,
            "wins": 0, "losses": 0, "win_rate": 0.0, "total_pnl": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "overrides": 0,
            "edge_working": False, "system_discipline": True,
            "douglas_verdict": "No trades yet in this sample.",
        }

    def _save(self) -> None:
        data = [asdict(e) for e in self._entries]
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._entries = [JournalEntry(**d) for d in data]
                logger.info(f"Journal loaded: {len(self._entries)} entries")
            except Exception as e:
                logger.warning(f"Journal load error: {e}")
