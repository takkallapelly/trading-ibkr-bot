"""src/risk/manager.py — Master RiskManager. Phase 5."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from loguru import logger
from src.config import settings, cfg
from src.strategy.signal import Signal
from src.risk.kelly import KellySizer
from src.risk.circuit_breaker import (
    DailyDrawdownBreaker, ConsecutiveLossBreaker,
    PortfolioHeatBreaker, CorrelationGuard,
)

@dataclass
class TradeOrder:
    signal: Signal
    approved: bool
    rejection_reason: str  = ""
    position_size_usd: float = 0.0
    shares: int            = 0
    entry_price: float     = 0.0
    stop_price: float      = 0.0
    target_price: float    = 0.0
    kelly_fraction: float  = 0.0
    win_rate_used: float   = 0.0
    capital_at_risk: float = 0.0

    @property
    def risk_usd(self):
        return abs(self.entry_price - self.stop_price) * self.shares

    def __repr__(self):
        if self.approved:
            return (f"TradeOrder ✅ {self.signal.ticker} {self.signal.direction.value} "
                    f"{self.shares}sh @${self.entry_price:.2f} risk=${self.risk_usd:.0f}")
        return f"TradeOrder ❌ {self.signal.ticker}: {self.rejection_reason}"


class RiskManager:
    def __init__(self, capital=None, config=None):
        self.capital = capital or settings.TOTAL_CAPITAL
        self.cfg     = config or cfg
        risk_cfg     = self.cfg.get("risk", {})

        self.sizer = KellySizer(
            capital=self.capital,
            kelly_fraction=risk_cfg.get("kelly_fraction", 0.5),
            max_position_usd=risk_cfg.get("max_position_usd", 2500.0),
            max_position_pct=settings.MAX_POSITION_PCT,
            min_trades=risk_cfg.get("kelly_lookback", 30),
        )
        self.drawdown_breaker    = DailyDrawdownBreaker(
            limit_pct=settings.DAILY_DRAWDOWN_LIMIT, capital=self.capital)
        self.loss_streak_breaker = ConsecutiveLossBreaker(
            max_losses=risk_cfg.get("consecutive_loss_halt", 5))
        self.heat_breaker        = PortfolioHeatBreaker(
            max_heat_pct=settings.MAX_PORTFOLIO_HEAT,
            capital=self.capital,
            max_positions=risk_cfg.get("max_open_positions", 3))
        self.correlation_guard   = CorrelationGuard(
            threshold=risk_cfg.get("correlation_threshold", 0.70))

        self._closed_trades : list[dict] = []
        self._open_positions: list[dict] = []

        logger.info(f"RiskManager ready | capital=${self.capital:,.0f} | "
                    f"kelly={self.sizer.kelly_fraction} | "
                    f"max_pos=${self.sizer.max_position_usd:,.0f}")

    def approve_entry(self, signal, daily_pnl=0.0,
                      open_positions=None, today=None):
        open_pos = open_positions or self._open_positions
        today    = today or date.today().isoformat()

        def reject(reason):
            logger.debug(f"RiskManager ❌ {signal.ticker}: {reason}")
            return TradeOrder(signal=signal, approved=False, rejection_reason=reason)

        if not signal.is_actionable:
            return reject(f"signal not actionable: {signal.blocked_reason}")

        halted, reason = self.drawdown_breaker.check(daily_pnl, today)
        if halted: return reject(reason)

        halted, reason = self.loss_streak_breaker.check()
        if halted: return reject(reason)

        stats        = self._trade_stats()
        position_usd = self.sizer.position_size_usd(
            win_rate=stats["win_rate"], avg_win=stats["avg_win"],
            avg_loss=stats["avg_loss"], trades_count=stats["n_trades"])

        if position_usd <= 0:
            return reject("Kelly sizing returned zero")

        halted, reason = self.heat_breaker.check(open_pos, position_usd)
        if halted: return reject(reason)

        open_tickers   = [p["ticker"] for p in open_pos]
        halted, reason = self.correlation_guard.check(signal.ticker, open_tickers)
        if halted: return reject(reason)

        shares = self.sizer.shares_to_buy(
            position_size_usd=position_usd,
            entry_price=signal.entry_price,
            risk_per_share=signal.risk_per_share)

        if shares < 1:
            return reject("position size too small (< 1 share)")

        order = TradeOrder(
            signal=signal, approved=True,
            position_size_usd=position_usd, shares=shares,
            entry_price=signal.entry_price, stop_price=signal.stop_price,
            target_price=signal.target_price,
            kelly_fraction=self.sizer.kelly_fraction,
            win_rate_used=stats["win_rate"],
            capital_at_risk=shares * signal.risk_per_share,
        )
        logger.info(f"RiskManager ✅ {signal.ticker} {signal.direction.value} | "
                    f"{shares}sh @${signal.entry_price:.2f} | "
                    f"size=${position_usd:,.0f} | risk=${order.risk_usd:.0f}")
        return order

    def record_trade_result(self, trade):
        pnl = trade.get("pnl", 0.0)
        self._closed_trades.append(trade)
        self.loss_streak_breaker.record(pnl)
        self._open_positions = [p for p in self._open_positions
                                if p.get("ticker") != trade.get("ticker")]

    def open_position(self, order):
        self._open_positions.append({
            "ticker": order.signal.ticker,
            "side":   order.signal.direction.value,
            "size_usd": order.position_size_usd,
            "shares": order.shares,
            "entry":  order.entry_price,
        })

    def close_position(self, ticker):
        self._open_positions = [p for p in self._open_positions
                                if p["ticker"] != ticker]

    def reset_daily(self):
        self.drawdown_breaker.reset()

    def reset_loss_streak(self):
        self.loss_streak_breaker.reset()

    def status(self):
        stats = self._trade_stats()
        return {
            "capital":           self.capital,
            "n_closed_trades":   len(self._closed_trades),
            "n_open_positions":  len(self._open_positions),
            "open_tickers":      [p["ticker"] for p in self._open_positions],
            "open_exposure_usd": sum(p["size_usd"] for p in self._open_positions),
            "win_rate":          stats["win_rate"],
            "loss_streak":       self.loss_streak_breaker.current_streak,
            "daily_halt":        self.drawdown_breaker._halted,
            "streak_halt":       self.loss_streak_breaker._halted,
        }

    def _trade_stats(self):
        closed = [t for t in self._closed_trades if t.get("pnl") is not None]
        if not closed:
            return {"n_trades":0,"win_rate":0.44,"avg_win":150.0,"avg_loss":100.0}
        wins   = [t["pnl"] for t in closed if t["pnl"] > 0]
        losses = [t["pnl"] for t in closed if t["pnl"] < 0]
        return {
            "n_trades": len(closed),
            "win_rate": len(wins)/len(closed),
            "avg_win":  sum(wins)/len(wins) if wins else 150.0,
            "avg_loss": abs(sum(losses)/len(losses)) if losses else 100.0,
        }
