"""
src/backtest/simulator.py
──────────────────────────
Bar-by-bar trade simulator.

Processes one ticker's historical DataFrame chronologically,
firing the SignalEngine on each bar and simulating realistic fills.

Key design principles (Ernie Chan — Quantitative Trading):
  - NO lookahead bias: signal at bar N can only enter at bar N+1 open
  - Realistic costs: commission per share + slippage in basis points
  - One position at a time per ticker
  - Stop and target checked against the NEXT bar's high/low

This is intentionally simple — one ticker, one thread.
The Backtester class runs multiple tickers and aggregates results.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from loguru import logger

from src.strategy.signals import SignalEngine
from src.strategy.signal import Direction


class TradeSimulator:
    """
    Simulates trading one ticker's historical data bar by bar.

    Args:
        commission : commission per share in USD (IBKR ~$0.005)
        slippage_bps : slippage in basis points (1bp = 0.01%)
        position_size_usd : fixed USD amount per trade (Phase 5 uses Kelly)
    """

    def __init__(
        self,
        commission: float = 0.005,
        slippage_bps: float = 5.0,
        position_size_usd: float = 2_500.0,
    ):
        self.commission       = commission
        self.slippage_bps     = slippage_bps
        self.position_size_usd = position_size_usd
        self.engine           = SignalEngine()

    def run(
        self,
        ticker: str,
        df: pd.DataFrame,
        start_idx: int = 0,
        end_idx: int | None = None,
    ) -> list[dict]:
        """
        Simulate all trades for one ticker over a date range.

        Args:
            ticker    : e.g. "TSLA"
            df        : feature-enriched OHLCV DataFrame (from DataLoader)
            start_idx : first bar index to start trading from
            end_idx   : last bar index (exclusive). None = end of data.

        Returns:
            List of completed trade dicts with entry, exit, pnl, etc.
        """
        end_idx  = end_idx or len(df)
        trades   = []
        position = None   # None = flat, dict = open position

        bars = df.iloc[start_idx:end_idx]

        for i in range(len(bars) - 1):   # -1 because we need next bar for fill
            current_bar = bars.iloc[i]
            next_bar    = bars.iloc[i + 1]

            # ── Check if open position hit stop or target ─────────────────────
            if position is not None:
                result = self._check_exit(position, next_bar)
                if result:
                    result["bars_held"] = i - position["entry_idx"]
                    trades.append(result)
                    position = None
                    continue

            # ── Look for new signal on current bar ────────────────────────────
            if position is None:
                signal = self.engine.evaluate(ticker, current_bar)

                # In backtesting skip the microstructure time guard
                # (daily bars have no intraday timestamp issue)
                long_only = self.engine.cfg.get("signals", {}).get("long_only", False)
                if (signal.direction != Direction.FLAT
                        and signal.score >= self.engine.min_score
                        and signal.stop_price > 0
                        and not (long_only and signal.direction.value == "SHORT")):

                    # Fill at next bar's open + slippage
                    fill_price = self._apply_slippage(
                        next_bar["open"], signal.direction
                    )

                    # Kelly-based position sizing from running trade history
                    from src.risk.kelly import KellySizer
                    completed = [t for t in trades if t.get("pnl") is not None]
                    wins   = [t["pnl"] for t in completed if t["pnl"] > 0]
                    losses = [t["pnl"] for t in completed if t["pnl"] < 0]
                    wr  = len(wins)/len(completed) if completed else 0.44
                    aw  = sum(wins)/len(wins)       if wins   else 150.0
                    al  = abs(sum(losses)/len(losses)) if losses else 100.0
                    sizer   = KellySizer(capital=25000.0, kelly_fraction=0.5,
                                         max_position_usd=self.position_size_usd)
                    pos_usd = sizer.position_size_usd(wr, aw, al, len(completed))
                    qty     = max(1, int(pos_usd / fill_price))
                    cost = qty * self.commission

                    position = {
                        "ticker":       ticker,
                        "side":         signal.direction.value,
                        "entry_time":   str(next_bar.name),
                        "entry_price":  fill_price,
                        "entry_idx":    i + 1,
                        "qty":          qty,
                        "stop_price":   signal.stop_price,
                        "target_price": signal.target_price,
                        "signal_score": signal.score,
                        "commission":   cost,
                    }

        # ── Force-close any open position at end of test period ───────────────
        if position is not None:
            last_bar = bars.iloc[-1]
            exit_price = self._apply_slippage(
                last_bar["close"], Direction.LONG
                if position["side"] == "SHORT" else Direction.SHORT
            )
            trades.append(self._close_trade(position, exit_price, last_bar.name, "EOD"))

        return trades

    # ── Private helpers ───────────────────────────────────────────────────────

    def _check_exit(self, position: dict, bar: pd.Series) -> dict | None:
        """
        Check if next bar's price action hits stop or target.
        Uses bar's HIGH and LOW — more realistic than close-to-close.
        """
        bar_high = bar["high"]
        bar_low  = bar["low"]
        stop     = position["stop_price"]
        target   = position["target_price"]

        if position["side"] == "LONG":
            if bar_low <= stop:
                return self._close_trade(position, stop, bar.name, "STOP_LOSS")
            if bar_high >= target:
                return self._close_trade(position, target, bar.name, "TAKE_PROFIT")

        else:  # SHORT
            if bar_high >= stop:
                return self._close_trade(position, stop, bar.name, "STOP_LOSS")
            if bar_low <= target:
                return self._close_trade(position, target, bar.name, "TAKE_PROFIT")

        return None

    def _close_trade(
        self,
        position: dict,
        exit_price: float,
        exit_time,
        exit_reason: str,
    ) -> dict:
        """Calculate P&L and return a completed trade dict."""
        qty       = position["qty"]
        entry     = position["entry_price"]
        side      = position["side"]
        direction = 1 if side == "LONG" else -1

        gross_pnl = direction * (exit_price - entry) * qty
        total_commission = position["commission"] + qty * self.commission
        net_pnl   = gross_pnl - total_commission

        return {
            **position,
            "exit_time":   str(exit_time),
            "exit_price":  exit_price,
            "pnl":         round(net_pnl, 2),
            "pnl_pct":     round(net_pnl / (entry * qty), 4),
            "gross_pnl":   round(gross_pnl, 2),
            "commission":  round(total_commission, 2),
            "exit_reason": exit_reason,
        }

    def _apply_slippage(self, price: float, direction: Direction) -> float:
        """
        Apply slippage in the direction that hurts us.
        Buying → price goes up. Selling → price goes down.
        """
        slip = price * (self.slippage_bps / 10_000)
        if direction == Direction.LONG:
            return round(price + slip, 4)
        else:
            return round(price - slip, 4)
