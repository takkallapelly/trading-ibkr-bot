"""
src/execution/live_trader.py
─────────────────────────────
LiveTrader — the master coordinator for live trading.

Connects all phases together:
  DataLoader (Phase 2) → SignalEngine (Phase 3) →
  RiskManager (Phase 5) → IBKRClient (Phase 7) →
  DataStore (Phase 2) → SelfLearner retrain (Phase 6)

Main loop runs every 5 minutes during market hours:
  1. Refresh latest 5-min bars from IBKR
  2. Run signal scan across all tickers
  3. For each signal → RiskManager approval
  4. For each approved order → IBKRClient bracket order
  5. Monitor open positions for stop/target hits
  6. Log everything to database
  7. Sunday: trigger ML retrain

Safety: TRADING_MODE=paper by default.
        Live orders require explicit mode=live + .env flag.
"""

from __future__ import annotations

import time
import threading
from datetime import datetime, date
from loguru import logger

from src.config import settings, cfg, TICKERS
from src.execution.scheduler import (
    is_market_open, is_tradable_time, is_sunday, market_status
)


class LiveTrader:
    """
    Master live trading coordinator.

    Usage:
        trader = LiveTrader(mode="paper")
        trader.start()   # blocks until KeyboardInterrupt
        trader.stop()    # graceful shutdown
    """

    SCAN_INTERVAL_SECS = 60 * 5   # scan every 5 minutes

    def __init__(self, mode: str = "paper"):
        self.mode    = mode
        self.is_live = mode.lower() == "live"
        self._running = False
        self._lock    = threading.Lock()

        logger.info(
            f"LiveTrader initialising | mode={mode} | "
            f"tickers={TICKERS}"
        )

        # ── Lazy imports (heavy — only load when actually trading) ────────────
        self._loader   = None
        self._engine   = None
        self._risk     = None
        self._store    = None
        self._learner  = None
        self._client   = None
        self._order_mgr = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """
        Start the live trading loop. Blocks until stop() is called.
        Call from run_live.py.
        """
        self._init_components()

        if self.is_live:
            if not self._connect_ibkr():
                logger.error("Failed to connect to IBKR — aborting")
                return
        else:
            logger.info("Paper mode — IBKR connection skipped")

        self._running = True
        logger.success(f"LiveTrader started | mode={self.mode}")

        self._run_loop()

    def stop(self) -> None:
        """Graceful shutdown — cancel all orders, disconnect."""
        logger.info("LiveTrader stopping...")
        self._running = False

        if self._client and self._client.is_connected():
            if not self.is_live:
                pass  # paper mode — no real orders
            else:
                logger.warning("Cancelling all open orders before disconnect")
                self._client.cancel_all_orders()
                time.sleep(1)
            self._client.disconnect()

        logger.info("LiveTrader stopped")

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Main trading loop — runs every 5 minutes during market hours."""
        last_retrain_date = None

        while self._running:
            try:
                status = market_status()

                # ── Pre-market: data refresh ──────────────────────────────────
                if not status["is_open"]:
                    logger.debug(
                        f"Market closed | {status['time_eastern']} | "
                        f"next open in {status['mins_to_open']:.0f}min"
                    )
                    self._daily_reset_if_needed()

                    # Sunday: trigger ML retrain
                    today = date.today().isoformat()
                    if is_sunday() and last_retrain_date != today:
                        logger.info("Sunday — triggering ML retrain")
                        self._retrain_ml()
                        last_retrain_date = today

                    time.sleep(60)
                    continue

                # ── During market hours ───────────────────────────────────────
                if not status["is_tradable"]:
                    logger.debug(
                        f"Market open but not tradable | {status['time_eastern']}"
                    )
                    time.sleep(30)
                    continue

                # Refresh latest intraday bars
                self._refresh_data()

                # Scan for signals
                signals = self._scan_signals()

                # Process each signal through risk manager
                for signal in signals:
                    self._process_signal(signal)

                # Monitor open positions
                self._monitor_positions()

                # Log equity snapshot
                self._save_equity_snapshot()

                logger.debug(
                    f"Scan complete | {status['time_eastern']} | "
                    f"open={len(self._order_mgr.open_orders())} positions | "
                    f"next scan in {self.SCAN_INTERVAL_SECS//60}min"
                )

                time.sleep(self.SCAN_INTERVAL_SECS)

            except KeyboardInterrupt:
                logger.info("Keyboard interrupt — stopping")
                break
            except Exception as e:
                logger.error(f"Loop error: {e}")
                time.sleep(30)  # brief pause before retry

    # ── Trading logic ─────────────────────────────────────────────────────────

    def _scan_signals(self) -> list:
        """Scan all tickers for actionable signals."""
        try:
            signals = self._engine.get_signals(loader=self._loader)
            return signals
        except Exception as e:
            logger.error(f"Signal scan error: {e}")
            return []

    def _process_signal(self, signal) -> None:
        """
        Process one signal through the full pipeline:
        RiskManager → MetaLabeler → IBKRClient
        """
        ticker = signal.ticker

        # Skip if already have an open position in this ticker
        if self._order_mgr.has_open_position(ticker):
            logger.debug(f"{ticker}: already have open position — skipping")
            return

        # Risk approval
        daily_pnl    = self._store.daily_pnl_today()
        open_pos     = self._order_mgr.open_positions_for_risk()
        order        = self._risk.approve_entry(
            signal,
            daily_pnl=daily_pnl,
            open_positions=open_pos,
        )

        if not order.approved:
            logger.debug(f"{ticker}: risk rejected — {order.rejection_reason}")
            return

        # Place the order
        self._place_order(order)

    def _place_order(self, order) -> None:
        """Place a bracket order via IBKR (or simulate in paper mode)."""
        from src.execution.order_manager import ManagedOrder, OrderState

        signal = order.signal
        ticker = signal.ticker

        managed = self._order_mgr.create(
            ticker       = ticker,
            side         = signal.direction.value,
            qty          = order.shares,
            entry_price  = order.entry_price,
            stop_price   = order.stop_price,
            target_price = order.target_price,
            signal_score = signal.score,
        )
        managed.transition(OrderState.PENDING)

        if self.is_live and self._client and self._client.is_connected():
            try:
                parent_id, stop_id, target_id = self._client.place_bracket_order(
                    ticker   = ticker,
                    qty      = order.shares,
                    entry    = order.entry_price,
                    stop     = order.stop_price,
                    target   = order.target_price,
                    side     = "BUY" if signal.direction.value == "LONG" else "SELL",
                )
                managed.parent_id = parent_id
                managed.stop_id   = stop_id
                managed.target_id = target_id
                managed.transition(OrderState.SUBMITTED)
            except Exception as e:
                logger.error(f"Order placement failed for {ticker}: {e}")
                return
        else:
            # Paper mode — simulate immediate fill at entry price
            managed.parent_id = self._order_mgr.next_paper_id()
            managed.stop_id   = managed.parent_id + 1
            managed.target_id = managed.parent_id + 2
            managed.transition(OrderState.SUBMITTED)
            managed.transition(
                OrderState.FILLED,
                fill_price=order.entry_price,
                fill_time=datetime.utcnow().isoformat(),
            )
            logger.info(
                f"[PAPER] {ticker} {signal.direction.value} {order.shares}sh "
                f"@ ${order.entry_price:.2f} | "
                f"stop=${order.stop_price:.2f} target=${order.target_price:.2f}"
            )

        # Register and save to DB
        self._order_mgr.register(managed)
        self._risk.open_position(order)

        trade_id = self._store.log_trade({
            "ticker":       ticker,
            "side":         signal.direction.value,
            "entry_time":   managed.fill_time or datetime.utcnow().isoformat(),
            "entry_price":  managed.fill_price or order.entry_price,
            "qty":          order.shares,
            "stop_price":   order.stop_price,
            "target_price": order.target_price,
            "signal_score": signal.score,
            "ibkr_order_id":managed.parent_id,
        })
        managed.db_trade_id = trade_id

    def _monitor_positions(self) -> None:
        """Check open positions against latest prices (paper mode)."""
        if self.is_live:
            return  # IBKR handles stop/target via bracket orders

        # Paper mode: check if stop or target hit on latest bar
        from src.execution.order_manager import OrderState
        for order in self._order_mgr.open_orders():
            if order.state != OrderState.FILLED:
                continue

            ticker = order.ticker
            bar    = self._loader.get_latest(ticker)
            if bar is None:
                continue

            high = float(bar.get("high", 0))
            low  = float(bar.get("low",  0))

            exit_price  = None
            exit_reason = None

            if order.side == "LONG":
                if low  <= order.stop_price:
                    exit_price, exit_reason = order.stop_price, "STOP_LOSS"
                elif high >= order.target_price:
                    exit_price, exit_reason = order.target_price, "TAKE_PROFIT"
            else:
                if high >= order.stop_price:
                    exit_price, exit_reason = order.stop_price, "STOP_LOSS"
                elif low  <= order.target_price:
                    exit_price, exit_reason = order.target_price, "TAKE_PROFIT"

            if exit_price:
                direction = 1 if order.side == "LONG" else -1
                pnl = direction * (exit_price - order.fill_price) * order.qty
                order.transition(
                    OrderState.CLOSED,
                    exit_price=exit_price,
                    exit_time=datetime.utcnow().isoformat(),
                    exit_reason=exit_reason,
                    pnl=round(pnl, 2),
                )

                # Update DB
                if hasattr(order, "db_trade_id"):
                    self._store.update_trade(order.db_trade_id, {
                        "exit_time":  order.exit_time,
                        "exit_price": exit_price,
                        "pnl":        pnl,
                        "pnl_pct":    pnl / (order.fill_price * order.qty),
                        "exit_reason":exit_reason,
                    })

                # Update risk manager
                self._risk.record_trade_result(order.to_trade_dict())
                self._risk.close_position(ticker)

                logger.info(
                    f"[PAPER] {ticker} CLOSED | {exit_reason} | "
                    f"pnl=${pnl:.2f}"
                )

    # ── Infrastructure ────────────────────────────────────────────────────────

    def _init_components(self) -> None:
        """Lazy-load all heavy components."""
        from src.data.loader import DataLoader
        from src.strategy.engine import StrategyEngine
        from src.risk.manager import RiskManager
        from src.data.store import DataStore
        from src.ml.learner import SelfLearner
        from src.execution.order_manager import OrderManager

        self._loader   = DataLoader(tickers=TICKERS, interval="5m")
        self._engine   = StrategyEngine(tickers=TICKERS)
        self._risk     = RiskManager(capital=settings.TOTAL_CAPITAL)
        self._store    = DataStore()
        self._learner  = SelfLearner()
        self._order_mgr = OrderManager()

        logger.info("All components initialised")

    def _connect_ibkr(self) -> bool:
        from src.execution.ibkr_client import IBKRClient
        self._client = IBKRClient()

        # Register fill callback
        self._client.on("fill", self._on_ibkr_fill)
        self._client.on("order_status", self._on_order_status)

        return self._client.connect_and_run()

    def _on_ibkr_fill(self, ticker, side, qty, price, exec_id, **_) -> None:
        """Called when IBKR reports a fill."""
        logger.info(f"IBKR fill | {ticker} {side} {qty}sh @ ${price:.2f}")

    def _on_order_status(self, order_id, status, filled, avg_fill, **_) -> None:
        """Called when order status changes."""
        if status == "Filled":
            order = self._order_mgr.on_fill(order_id, avg_fill)
            if order:
                from src.execution.order_manager import OrderState
                if order.state == OrderState.CLOSED:
                    self._risk.record_trade_result(order.to_trade_dict())
                    self._risk.close_position(order.ticker)

    def _refresh_data(self) -> None:
        """Refresh latest bars for all tickers."""
        try:
            self._loader.refresh_all(days=2)
        except Exception as e:
            logger.warning(f"Data refresh error: {e}")

    def _retrain_ml(self) -> None:
        """Trigger ML retrain."""
        try:
            result = self._learner.retrain()
            if result.get("status") == "success":
                logger.success(
                    f"ML retrain complete | accuracy={result['accuracy']:.1%}"
                )
        except Exception as e:
            logger.error(f"ML retrain failed: {e}")

    def _daily_reset_if_needed(self) -> None:
        """Reset daily circuit breakers at market open."""
        now = datetime.now()
        if now.hour == 9 and now.minute < 10:
            self._risk.reset_daily()

    def _save_equity_snapshot(self) -> None:
        """Save daily equity curve point."""
        try:
            daily_pnl = self._store.daily_pnl_today()
            equity    = settings.TOTAL_CAPITAL + daily_pnl
            open_n    = len(self._order_mgr.open_orders())
            self._store.save_equity_snapshot(equity, daily_pnl, open_n)
        except Exception as e:
            logger.debug(f"Equity snapshot error: {e}")


# Monkey-patch OrderManager with paper ID counter
def _next_paper_id(self):
    if not hasattr(self, "_paper_id"):
        self._paper_id = 1000
    self._paper_id += 3
    return self._paper_id

from src.execution.order_manager import OrderManager
OrderManager.next_paper_id = _next_paper_id
