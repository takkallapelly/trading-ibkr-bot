"""
src/strategy/engine.py
───────────────────────
StrategyEngine — the top-level class that combines:
  1. SignalEngine     (3-layer RSI/BB/EMA signals)
  2. MetaLabeler      (ML vetting — pass-through until Phase 6)
  3. DataLoader       (fetches latest bars)

This is what the live bot and backtester both call.
One method: get_signals() → list[Signal]

Usage:
    engine = StrategyEngine()
    signals = engine.get_signals()      # scan all tickers right now
    signals = engine.get_signals(["TSLA", "NVDA"])  # specific tickers
"""

from __future__ import annotations

from loguru import logger
from rich.console import Console
from rich.table import Table

from src.config import cfg, TICKERS
from src.strategy.signals import SignalEngine
from src.strategy.meta_labeler import MetaLabeler
from src.strategy.signal import Signal

console = Console()


class StrategyEngine:
    """
    Master strategy engine — the single entry point for the live bot
    and backtester to get trade signals.

    Args:
        tickers : tickers to scan. Defaults to config universe.
        config  : override config dict (useful for testing).
    """

    def __init__(
        self,
        tickers: list[str] | None = None,
        config: dict | None = None,
    ):
        self.tickers      = tickers or TICKERS
        self.cfg          = config or cfg
        self.signal_engine = SignalEngine(config=self.cfg)
        self.meta_labeler  = MetaLabeler()

        logger.info(
            f"StrategyEngine ready | {len(self.tickers)} tickers | "
            f"meta_labeler={'active' if self.meta_labeler.is_active else 'pass-through'}"
        )

    def get_signals(
        self,
        tickers: list[str] | None = None,
        loader=None,          # DataLoader instance
        bars: dict | None = None,  # pre-loaded bars (for backtesting)
        print_table: bool = False,
    ) -> list[Signal]:
        """
        Scan tickers and return actionable signals after meta-labeling.

        Args:
            tickers     : override ticker list for this call
            loader      : DataLoader to fetch latest bars from
            bars        : pre-loaded {ticker: bar_series} dict (backtesting)
            print_table : print a summary table to the terminal

        Returns:
            List of actionable Signal objects, sorted by score descending.
        """
        scan_tickers = tickers or self.tickers

        # ── Inject SPY regime RSI into config for Rule 7 ─────────────────────
        spy_rsi = self._get_spy_rsi(bars, loader)
        if spy_rsi is not None:
            self.cfg = {**self.cfg, "_spy_rsi": spy_rsi}
            logger.debug(f"SPY regime RSI(2)={spy_rsi:.1f}")
        else:
            self.cfg.pop("_spy_rsi", None)

        # ── Get latest bars ───────────────────────────────────────────────────
        if bars is None:
            if loader is None:
                # Import here to avoid circular imports
                from src.data.loader import DataLoader
                loader = DataLoader(tickers=scan_tickers)
            bars = {
                t: loader.get_latest(t)
                for t in scan_tickers
                if loader.get_latest(t) is not None
            }

        if not bars:
            logger.warning("StrategyEngine: no bars available to scan")
            return []

        # ── Layer 1+2+3: signal engine scan ───────────────────────────────────
        raw_signals = self.signal_engine.scan(bars)

        # ── Meta-labeler vetting ──────────────────────────────────────────────
        approved = self.meta_labeler.approve_all(raw_signals)

        if print_table:
            self._print_signals(bars, approved)

        return approved

    def scan_all_and_print(self, loader=None) -> list[Signal]:
        """
        Convenience method: scan all tickers and print a full table
        showing every ticker's current signal state, not just actionable ones.
        Useful for monitoring from the terminal.
        """
        from src.data.loader import DataLoader
        loader = loader or DataLoader(tickers=self.tickers)

        all_bars = {
            t: loader.get_latest(t)
            for t in self.tickers
            if loader.get_latest(t) is not None
        }

        # Evaluate every ticker, even non-actionable
        all_signals = [
            self.signal_engine.evaluate(ticker, bar)
            for ticker, bar in all_bars.items()
        ]

        # Print full table
        table = Table(title="Signal scan — all tickers", show_lines=True)
        table.add_column("Ticker",    style="cyan",    no_wrap=True)
        table.add_column("Direction", style="white")
        table.add_column("Score",     style="yellow",  justify="right")
        table.add_column("RSI(2)",    style="magenta", justify="right")
        table.add_column("BB%",       style="blue",    justify="right")
        table.add_column("Entry",     style="green",   justify="right")
        table.add_column("Stop",      style="red",     justify="right")
        table.add_column("Target",    style="green",   justify="right")
        table.add_column("R:R",       style="white",   justify="right")
        table.add_column("Status",    style="white")

        for s in sorted(all_signals, key=lambda x: abs(x.score), reverse=True):
            if s.direction.value == "FLAT":
                direction_str = "—"
                status = f"[dim]{s.blocked_reason or 'no signal'}[/dim]"
            elif s.is_actionable:
                direction_str = f"[green]{s.direction.value}[/green]"
                status = "[green]✅ ACTIONABLE[/green]"
            else:
                direction_str = f"[dim]{s.direction.value}[/dim]"
                status = f"[red]❌ {s.blocked_reason}[/red]"

            table.add_row(
                s.ticker,
                direction_str,
                f"{s.score:.2f}",
                f"{s.rsi:.1f}",
                f"{s.bb_pct:.2f}",
                f"${s.entry_price:.2f}" if s.entry_price else "—",
                f"${s.stop_price:.2f}"  if s.stop_price  else "—",
                f"${s.target_price:.2f}"if s.target_price else "—",
                f"{s.risk_reward}"      if s.risk_reward  else "—",
                status,
            )

        console.print(table)

        # Return only actionable ones
        return [s for s in all_signals if s.is_actionable]

    # ── Private ───────────────────────────────────────────────────────────────

    def _get_spy_rsi(self, bars: dict | None, loader) -> float | None:
        """
        Return the current SPY RSI(2) for the regime filter.
        Reads from pre-loaded bars first, then falls back to loader.
        Returns None if SPY data is unavailable (filter is skipped).
        """
        # Already in the pre-loaded bars dict (backtest or intraday scan)
        if bars and "SPY" in bars:
            spy_bar = bars["SPY"]
            rsi = spy_bar.get("rsi_2")
            return float(rsi) if rsi is not None else None

        # Try to fetch from loader
        try:
            if loader is None:
                from src.data.loader import DataLoader
                loader = DataLoader(tickers=["SPY"])
            spy_bar = loader.get_latest("SPY")
            if spy_bar is not None:
                rsi = spy_bar.get("rsi_2")
                return float(rsi) if rsi is not None else None
        except Exception as e:
            logger.debug(f"SPY regime: could not fetch RSI: {e}")

        return None

    def _print_signals(
        self,
        bars: dict,
        signals: list[Signal],
    ) -> None:
        if not signals:
            console.print("[dim]No actionable signals[/dim]")
            return

        table = Table(title="Actionable signals", show_lines=True)
        table.add_column("Ticker",    style="cyan")
        table.add_column("Direction", style="white")
        table.add_column("Score",     style="yellow",  justify="right")
        table.add_column("Entry",     style="green",   justify="right")
        table.add_column("Stop",      style="red",     justify="right")
        table.add_column("Target",    style="green",   justify="right")
        table.add_column("R:R",       style="white",   justify="right")

        for s in signals:
            table.add_row(
                s.ticker,
                s.direction.value,
                f"{s.score:.2f}",
                f"${s.entry_price:.2f}",
                f"${s.stop_price:.2f}",
                f"${s.target_price:.2f}",
                str(s.risk_reward),
            )

        console.print(table)
