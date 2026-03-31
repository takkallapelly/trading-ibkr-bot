"""
src/strategy/signals.py
────────────────────────
SignalEngine — reads feature bars, applies the 3-layer signal
architecture, runs guard rules, and returns Signal objects.

3-layer architecture:
  Layer 1 (50%) — RSI(2) extreme reversal       primary driver
  Layer 2 (30%) — Bollinger Band touch           confirmation
  Layer 3 (20%) — EMA 9×20 momentum cross       direction filter

The composite score from Phase 2 features does the math.
This engine reads that score and converts it into actionable Signals.
"""

from __future__ import annotations

import pandas as pd
from loguru import logger

from src.config import cfg
from src.strategy.signal import Signal, Direction, flat_signal
from src.strategy.rules import apply_all_rules


class SignalEngine:
    """
    Converts a feature-enriched price bar into a trade Signal.

    Usage:
        engine = SignalEngine()
        signal = engine.evaluate("TSLA", bar)
        if signal.is_actionable:
            # send to risk manager and executor
    """

    def __init__(self, config: dict | None = None):
        self.cfg  = config or cfg
        self._sig = self.cfg.get("signals", {})
        self._risk = self.cfg.get("risk", {})

        self.min_score      = self._sig.get("min_signal_score", 0.60)
        self.stop_atr_mult  = self._risk.get("stop_loss_atr_mult", 1.0)
        self.tp_atr_mult    = self._risk.get("take_profit_atr_mult", 1.5)

        logger.info(
            f"SignalEngine ready | min_score={self.min_score} | "
            f"stop={self.stop_atr_mult}×ATR | target={self.tp_atr_mult}×ATR"
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def evaluate(self, ticker: str, bar: pd.Series) -> Signal:
        """
        Evaluate one price bar and return a Signal.

        Args:
            ticker : e.g. "TSLA"
            bar    : latest row from DataLoader.get(ticker)

        Returns:
            Signal object — check signal.is_actionable before trading
        """
        # ── 1. Read composite score from features ─────────────────────────────
        score     = float(bar.get("signal_score", 0.0))
        close     = float(bar.get("close", 0.0))
        atr       = float(bar.get("atr", 0.0))
        rsi       = float(bar.get("rsi_2", 50.0))
        bb_pct    = float(bar.get("bb_pct", 0.5))
        ema_diff  = float(bar.get("ema_diff", 0.0))
        vol_ratio = float(bar.get("vol_ratio", 1.0))

        if close <= 0 or atr <= 0:
            return flat_signal(ticker, "invalid bar data", bar.name)

        # ── 2. Determine direction from score sign ────────────────────────────
        if score >= self.min_score:
            direction = Direction.LONG
        elif score <= -self.min_score:
            direction = Direction.SHORT
        else:
            return flat_signal(
                ticker,
                f"score {score:.2f} below threshold ±{self.min_score}",
                bar.name,
            )

        # ── 3. Calculate entry, stop, and target prices ───────────────────────
        entry, stop, target = self._price_levels(close, atr, direction)

        # ── 4. Run all guard rules ────────────────────────────────────────────
        blocked, reason = apply_all_rules(
            bar=bar,
            score=abs(score),
            direction=direction.value,
            entry=entry,
            stop=stop,
            target=target,
            cfg=self.cfg,
        )

        signal = Signal(
            ticker       = ticker,
            direction    = direction,
            score        = abs(score),
            timestamp    = bar.name,
            close        = close,
            entry_price  = entry,
            stop_price   = stop,
            target_price = target,
            atr          = atr,
            rsi          = rsi,
            bb_pct       = bb_pct,
            ema_diff     = ema_diff,
            vol_ratio    = vol_ratio,
            blocked_reason = reason if blocked else "",
        )

        if signal.is_actionable:
            logger.info(
                f"Signal ✅ {ticker} {direction.value} | "
                f"score={abs(score):.2f} rsi={rsi:.1f} | "
                f"entry={entry:.2f} stop={stop:.2f} target={target:.2f} "
                f"R:R={signal.risk_reward}"
            )
        else:
            logger.debug(
                f"Signal ❌ {ticker} BLOCKED | {reason or 'score too low'}"
            )

        return signal

    def scan(
        self,
        bars: dict[str, pd.Series],
    ) -> list[Signal]:
        """
        Scan multiple tickers and return all actionable signals,
        sorted by score descending (strongest signal first).

        Args:
            bars : dict {ticker: latest_bar_series}

        Returns:
            List of actionable Signal objects, strongest first.
        """
        signals = []
        for ticker, bar in bars.items():
            signal = self.evaluate(ticker, bar)
            if signal.is_actionable:
                signals.append(signal)

        signals.sort(key=lambda s: s.score, reverse=True)

        if signals:
            logger.info(
                f"Scan complete | {len(signals)} actionable signals: "
                + ", ".join(f"{s.ticker}({s.direction.value} {s.score:.2f})"
                            for s in signals)
            )
        else:
            logger.info("Scan complete | no actionable signals")

        return signals

    def evaluate_all(
        self,
        loader,  # DataLoader instance
    ) -> list[Signal]:
        """
        Convenience method: load latest bars from DataLoader and scan all.

        Usage:
            from src.data.loader import DataLoader
            loader = DataLoader()
            signals = engine.evaluate_all(loader)
        """
        bars = {}
        for ticker in loader.tickers:
            bar = loader.get_latest(ticker)
            if bar is not None:
                bars[ticker] = bar

        return self.scan(bars)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _price_levels(
        self,
        close: float,
        atr: float,
        direction: Direction,
    ) -> tuple[float, float, float]:
        """
        Calculate entry, stop loss, and take profit prices.

        For LONG:
          entry  = close price (market order)
          stop   = entry - (stop_mult × ATR)
          target = entry + (tp_mult × ATR)

        For SHORT:
          entry  = close price
          stop   = entry + (stop_mult × ATR)
          target = entry - (tp_mult × ATR)

        Using ATR-based stops (not fixed %) means:
          - Volatile stocks get wider stops automatically
          - Quiet stocks get tighter stops automatically
          - Consistent risk across the whole portfolio
        """
        stop_dist   = self.stop_atr_mult * atr
        target_dist = self.tp_atr_mult   * atr

        entry = round(close, 2)

        if direction == Direction.LONG:
            stop   = round(entry - stop_dist, 2)
            target = round(entry + target_dist, 2)
        else:  # SHORT
            stop   = round(entry + stop_dist, 2)
            target = round(entry - target_dist, 2)

        return entry, stop, target

