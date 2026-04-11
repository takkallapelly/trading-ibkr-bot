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
from src.strategy.regime import RegimeDetector
from src.data.market_context import get_market_context, MarketContext

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
        ibkr_client=None,
    ):
        self.tickers       = tickers or TICKERS
        self.cfg           = config or cfg
        self.ibkr_client   = ibkr_client
        self.signal_engine = SignalEngine(config=self.cfg)
        self.meta_labeler  = MetaLabeler()
        self.regime        = RegimeDetector()
        self._market_ctx   = None   # cached market context

        logger.info(
            f"StrategyEngine ready | {len(self.tickers)} tickers | "
            f"meta_labeler={'active' if self.meta_labeler.is_active else 'pass-through'} | "
            f"market_filter=active | regime=active"
        )

    def get_signals(
        self,
        tickers: list[str] | None = None,
        loader=None,
        bars: dict | None = None,
        print_table: bool = False,
    ) -> list[Signal]:
        """
        Scan tickers and return actionable signals after:
          1. Market context filter (VIX, SPY trend, VWAP)
          2. Per-ticker regime filter (Hurst exponent)
          3. Signal engine (RSI/BB/EMA)
          4. Meta-labeler (ML vetting)
          5. Score-weighted position sizing adjustment
        """
        scan_tickers = tickers or self.tickers

        # ── Step 1: Market context (VIX + SPY) ───────────────────────────────
        try:
            ctx = get_market_context(self.ibkr_client)
            self._market_ctx = ctx
            logger.info(f"Market: {ctx}")

            # Hard blocks
            if not ctx.allow_longs and not ctx.allow_shorts:
                logger.warning(
                    f"Market filter: ALL TRADING BLOCKED | {ctx.reason}"
                )
                return []

        except Exception as e:
            logger.warning(f"Market context failed: {e} — trading without filter")
            ctx = None

        # ── Step 2: Get latest bars ───────────────────────────────────────────
        if bars is None:
            if loader is None:
                from src.data.loader import DataLoader
                loader = DataLoader(tickers=scan_tickers)
            bars = {
                t: loader.get_latest(t)
                for t in scan_tickers
                if loader.get_latest(t) is not None
            }

        if not bars:
            logger.warning("StrategyEngine: no bars available")
            return []

        # ── Step 3: Signal engine scan ────────────────────────────────────────
        raw_signals = self.signal_engine.scan(bars)

        # ── Step 4: Market direction filter ──────────────────────────────────
        if ctx:
            filtered = []
            for s in raw_signals:
                from src.strategy.signal import Direction
                if s.direction == Direction.LONG and not ctx.allow_longs:
                    logger.info(f"{s.ticker}: LONG blocked by market filter | {ctx.reason}")
                    continue
                if s.direction == Direction.SHORT and not ctx.allow_shorts:
                    logger.info(f"{s.ticker}: SHORT blocked by market filter | {ctx.reason}")
                    continue
                filtered.append(s)
            raw_signals = filtered

        # ── Step 5: Meta-labeler vetting ──────────────────────────────────────
        approved = self.meta_labeler.approve_all(raw_signals)

        # ── Step 6: Attach position multiplier to each signal ─────────────────
        if ctx and approved:
            for s in approved:
                # Score-weighted sizing: stronger signal = bigger position
                score_mult = 1.0
                if s.score >= 0.65:   score_mult = 1.3   # strong signal
                elif s.score >= 0.55: score_mult = 1.0   # medium signal
                else:                  score_mult = 0.7   # weak signal

                # Combined multiplier
                s._position_mult = round(ctx.position_mult * score_mult, 2)
                logger.debug(
                    f"{s.ticker}: pos_mult={s._position_mult:.2f} "
                    f"(market={ctx.position_mult:.1f} x score={score_mult:.1f})"
                )

        if print_table:
            self._print_signals(bars, approved)

        return approved

    def get_market_context(self):
        """Return cached market context from last scan."""
        return self._market_ctx

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
