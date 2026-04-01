"""
src/execution/live_trader.py
─────────────────────────────
LiveTrader — master coordinator for live/paper trading.
Now uses proper 5-minute intraday bars for signal scanning.

Loop (every 5 minutes during market hours):
  1. Refresh 5-min bars for all tickers
  2. Compute intraday features (RSI, BB, EMA, ATR on 5-min bars)
  3. Scan signals → RiskManager → IBKRClient bracket orders
  4. Monitor open positions
  5. Save equity snapshot
  Sunday: retrain ML model
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
    Master live/paper trading coordinator with 5-min intraday scanning.

    Usage:
        trader = LiveTrader(mode="paper")
        trader.start()   # blocks — press Ctrl+C to stop
        trader.stop()    # graceful shutdown
    """

    SCAN_INTERVAL_SECS = 60 * 5   # scan every 5 minutes

    def __init__(self, mode: str = "paper"):
        self.mode    = mode
        self.is_live = mode.lower() == "live"
        self._running = False

        logger.info(
            f"LiveTrader initialising | mode={mode} | "
            f"tickers={TICKERS}"
        )

        # All components initialised lazily in start()
        self._intraday  = None   # IntradayFetcher (5-min bars)
        self._engine    = None   # StrategyEngine
        self._risk      = None   # RiskManager
        self._store     = None   # DataStore
        self._learner   = None   # SelfLearner (ML)
        self._client    = None   # IBKRClient
        self._order_mgr = None   # OrderManager
        self._alerts    = None   # AlertSystem

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the live trading loop. Blocks until stopped."""
        self._init_components()

        if self.is_live:
            if not self._connect_ibkr():
                logger.error("IBKR connection failed — aborting")
                return
        else:
            logger.info("Paper mode — IBKR connection skipped (using yfinance)")

        # Warm up 5-min bar history before entering loop
        logger.info("Warming up intraday data (5-min bars)...")
        self._intraday.warmup(days=5)

        if not self._intraday.is_ready(min_bars=25):
            logger.warning(
                "Not enough intraday bars loaded — "
                "signals may be unreliable until more bars accumulate"
            )

        self._running = True
        self._alerts.bot_started(self.mode)
        logger.success(f"LiveTrader started | mode={self.mode}")

        self._run_loop()

    def stop(self) -> None:
        """Graceful shutdown."""
        logger.info("LiveTrader stopping...")
        self._running = False

        if self._client and self._client.is_connected():
            if self.is_live:
                logger.warning("Cancelling all open orders before disconnect")
                self._client.cancel_all_orders()
                time.sleep(1)
            self._client.disconnect()

        if self._alerts:
            self._alerts.bot_stopped()

        logger.info("LiveTrader stopped")

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """5-minute scanning loop."""
        last_retrain_date  = None
        last_summary_date  = None
        scan_count         = 0

        while self._running:
            try:
                status = market_status()

                # ── Outside market hours ──────────────────────────────────────
                if not status["is_open"]:
                    mins = status["mins_to_open"]
                    logger.info(
                        f"Market closed | {status['time_eastern']} | "
                        + (f"opens in {mins:.0f} min" if mins > 0 else "checking...")
                    )

                    # Daily reset at start of new day
                    self._daily_reset_if_needed()

                    # Sunday ML retrain
                    today = date.today().isoformat()
                    if is_sunday() and last_retrain_date != today:
                        logger.info("Sunday — triggering ML retrain")
                        self._retrain_ml()
                        last_retrain_date = today

                    time.sleep(60)
                    continue

                # ── Microstructure guard (Harris) ─────────────────────────────
                if not status["is_tradable"]:
                    logger.info(
                        f"Market open but avoiding microstructure window | "
                        f"{status['time_eastern']}"
                    )
                    time.sleep(30)
                    continue

                # ── Refresh 5-min intraday bars ───────────────────────────────
                scan_count += 1
                logger.info(
                    f"Scan #{scan_count} | {status['time_eastern']} | "
                    f"open positions: {len(self._order_mgr.open_orders())}"
                )

                self._refresh_intraday()

                if not self._intraday.is_ready(min_bars=25):
                    logger.warning("Insufficient intraday bars — skipping scan")
                    time.sleep(self.SCAN_INTERVAL_SECS)
                    continue

                # ── Signal scan on 5-min bars ─────────────────────────────────
                signals = self._scan_intraday_signals()

                # ── Process signals ───────────────────────────────────────────
                for signal in signals:
                    self._process_signal(signal)

                # ── Monitor open positions ────────────────────────────────────
                self._monitor_positions()

                # ── Save equity snapshot ──────────────────────────────────────
                self._save_equity_snapshot()

                # ── End of day summary at 4pm ET ──────────────────────────────
                today = date.today().isoformat()
                if status["mins_to_close"] < 1 and last_summary_date != today:
                    self._send_daily_summary()
                    last_summary_date = today

                time.sleep(self.SCAN_INTERVAL_SECS)

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Loop error: {e}")
                time.sleep(30)

    # ── Intraday signal scanning ───────────────────────────────────────────────

    def _refresh_intraday(self) -> None:
        """Fetch latest 5-min bars and recompute features."""
        try:
            self._intraday.refresh_all(days=2)
        except Exception as e:
            logger.warning(f"Intraday refresh error: {e}")

    def _scan_intraday_signals(self) -> list:
        """
        Scan all tickers using latest 5-min bars.
        Uses intraday-calibrated thresholds (wider RSI, 5-min ATR).
        """
        try:
            latest_bars = self._intraday.get_latest_all()

            if not latest_bars:
                logger.warning("No intraday bars available for scan")
                return []

            # Log current bar values for visibility
            for ticker, bar in latest_bars.items():
                rsi   = bar.get("rsi_2", 50)
                score = bar.get("signal_score", 0)
                close = bar.get("close", 0)
                logger.debug(
                    f"{ticker} | close=${close:.2f} | "
                    f"rsi={rsi:.1f} | score={score:.2f}"
                )

            signals = self._engine.get_signals(bars=latest_bars)

            if signals:
                logger.info(
                    f"Actionable signals: "
                    + ", ".join(
                        f"{s.ticker}({s.direction.value} score={s.score:.2f} "
                        f"rsi={s.rsi:.1f})"
                        for s in signals
                    )
                )
            else:
                logger.info("No actionable signals this scan")

            return signals

        except Exception as e:
            logger.error(f"Signal scan error: {e}")
            return []

    def _process_signal(self, signal) -> None:
        """Process one signal through risk manager → order placement."""
        ticker = signal.ticker

        if self._order_mgr.has_open_position(ticker):
            logger.debug(f"{ticker}: position already open — skipping")
            return

        daily_pnl = self._store.daily_pnl_today()
        open_pos  = self._order_mgr.open_positions_for_risk()
        order     = self._risk.approve_entry(
            signal,
            daily_pnl=daily_pnl,
            open_positions=open_pos,
        )

        if not order.approved:
            logger.debug(f"{ticker}: risk rejected — {order.rejection_reason}")
            return

        self._place_order(order)

    def _place_order(self, order) -> None:
        """Place bracket order via IBKR or simulate in paper mode."""
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
                    ticker  = ticker,
                    qty     = order.shares,
                    entry   = order.entry_price,
                    stop    = order.stop_price,
                    target  = order.target_price,
                    side    = "BUY" if signal.direction.value == "LONG" else "SELL",
                )
                managed.parent_id = parent_id
                managed.stop_id   = stop_id
                managed.target_id = target_id
                managed.transition(OrderState.SUBMITTED)
            except Exception as e:
                logger.error(f"Order placement failed {ticker}: {e}")
                return
        else:
            # Paper mode — simulate immediate fill
            managed.parent_id = self._order_mgr.next_paper_id()
            managed.stop_id   = managed.parent_id + 1
            managed.target_id = managed.parent_id + 2
            managed.transition(OrderState.SUBMITTED)
            managed.transition(
                OrderState.FILLED,
                fill_price = order.entry_price,
                fill_time  = datetime.utcnow().isoformat(),
            )
            logger.info(
                f"📋 [PAPER] {ticker} {signal.direction.value} "
                f"{order.shares}sh @ ${order.entry_price:.2f} | "
                f"stop=${order.stop_price:.2f} "
                f"target=${order.target_price:.2f} | "
                f"risk=${order.risk_usd:.0f}"
            )

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

        # Telegram alert
        self._alerts.trade_opened(signal, order)

    def _monitor_positions(self) -> None:
        """Check open positions against latest 5-min bar (paper mode)."""
        if self.is_live:
            return  # IBKR handles via bracket orders

        from src.execution.order_manager import OrderState
        for order in self._order_mgr.open_orders():
            if order.state != OrderState.FILLED:
                continue

            ticker = order.ticker
            bar    = self._intraday.get_latest(ticker)
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
                    exit_price  = exit_price,
                    exit_time   = datetime.utcnow().isoformat(),
                    exit_reason = exit_reason,
                    pnl         = round(pnl, 2),
                )

                if hasattr(order, "db_trade_id"):
                    self._store.update_trade(order.db_trade_id, {
                        "exit_time":  order.exit_time,
                        "exit_price": exit_price,
                        "pnl":        pnl,
                        "pnl_pct":    pnl / (order.fill_price * order.qty),
                        "exit_reason":exit_reason,
                    })

                self._risk.record_trade_result(order.to_trade_dict())
                self._risk.close_position(ticker)

                emoji = "✅" if pnl >= 0 else "❌"
                logger.info(
                    f"{emoji} [PAPER] {ticker} CLOSED | "
                    f"{exit_reason} | pnl=${pnl:.2f}"
                )
                self._alerts.trade_closed(
                    ticker=ticker, pnl=pnl,
                    exit_reason=exit_reason,
                    entry_price=order.fill_price,
                    exit_price=exit_price,
                    qty=order.qty,
                )

    # ── Infrastructure ────────────────────────────────────────────────────────

    def _init_components(self) -> None:
        from src.data.intraday import IntradayFetcher, _intraday_cfg
        from src.strategy.engine import StrategyEngine
        from src.risk.manager import RiskManager
        from src.data.store import DataStore
        from src.ml.learner import SelfLearner
        from src.execution.order_manager import OrderManager
        from src.dashboard.alerts import AlertSystem

        intraday_config = _intraday_cfg()

        self._intraday  = IntradayFetcher(
            tickers  = TICKERS,
            use_ibkr = self.is_live,
            ibkr_client = self._client,
        )
        self._engine    = StrategyEngine(tickers=TICKERS, config=intraday_config)
        self._risk      = RiskManager(capital=settings.TOTAL_CAPITAL)
        self._store     = DataStore()
        self._learner   = SelfLearner()
        self._order_mgr = OrderManager()
        self._alerts    = AlertSystem()

        logger.info(
            f"Components ready | intraday=5-min | "
            f"rsi_oversold=15 | rsi_overbought=85 | "
            f"stop=1.5×ATR | target=3.0×ATR"
        )

    def _connect_ibkr(self) -> bool:
        from src.execution.ibkr_client import IBKRClient
        self._client = IBKRClient()
        self._client.on("fill",         self._on_ibkr_fill)
        self._client.on("order_status", self._on_order_status)
        connected = self._client.connect_and_run()
        if connected:
            # Update intraday fetcher with connected client
            self._intraday.client   = self._client
            self._intraday.use_ibkr = True
        return connected

    def _on_ibkr_fill(self, ticker, side, qty, price, **_) -> None:
        logger.info(f"IBKR fill | {ticker} {side} {qty}sh @ ${price:.2f}")

    def _on_order_status(self, order_id, status, filled, avg_fill, **_) -> None:
        if status == "Filled":
            order = self._order_mgr.on_fill(order_id, avg_fill)
            if order:
                from src.execution.order_manager import OrderState
                if order.state == OrderState.CLOSED:
                    self._risk.record_trade_result(order.to_trade_dict())
                    self._risk.close_position(order.ticker)

    def _retrain_ml(self) -> None:
        try:
            result = self._learner.retrain()
            if result.get("status") == "success":
                acc = result["accuracy"]
                n   = result["n_trades"]
                logger.success(f"ML retrain | accuracy={acc:.1%} | n={n}")
                self._alerts.ml_retrained(acc, n)
        except Exception as e:
            logger.error(f"ML retrain failed: {e}")

    def _daily_reset_if_needed(self) -> None:
        now = datetime.now()
        if now.hour == 9 and now.minute < 10:
            self._risk.reset_daily()
            logger.info("Daily circuit breakers reset")

    def _save_equity_snapshot(self) -> None:
        try:
            daily_pnl = self._store.daily_pnl_today()
            equity    = settings.TOTAL_CAPITAL + daily_pnl
            open_n    = len(self._order_mgr.open_orders())
            self._store.save_equity_snapshot(equity, daily_pnl, open_n)
        except Exception as e:
            logger.debug(f"Equity snapshot: {e}")

    def _send_daily_summary(self) -> None:
        try:
            daily_pnl    = self._store.daily_pnl_today()
            open_trades  = self._store.open_trades()
            trades_today = self._store.load_trades(closed_only=True)
            n_today      = len(trades_today)
            wr           = self._risk._trade_stats()["win_rate"]
            equity       = settings.TOTAL_CAPITAL + daily_pnl
            open_tickers = open_trades["ticker"].tolist() if not open_trades.empty else []

            self._alerts.daily_summary(
                daily_pnl=daily_pnl, total_equity=equity,
                n_trades=n_today, win_rate=wr,
                open_positions=open_tickers,
            )
        except Exception as e:
            logger.debug(f"Daily summary error: {e}")


# Monkey-patch OrderManager with paper ID counter
def _next_paper_id(self):
    if not hasattr(self, "_paper_id"): self._paper_id = 1000
    self._paper_id += 3
    return self._paper_id

from src.execution.order_manager import OrderManager
OrderManager.next_paper_id = _next_paper_id
