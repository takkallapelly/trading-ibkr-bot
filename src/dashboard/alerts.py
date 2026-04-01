"""
src/dashboard/alerts.py
────────────────────────
Telegram alert system — sends notifications for key bot events.

Alerts sent:
  - New trade opened (entry, stop, target, signal score)
  - Trade closed (P&L, exit reason)
  - Daily summary at 4pm ET
  - Circuit breaker triggered (drawdown halt, loss streak)
  - ML model retrained (accuracy, n_trades)

Setup:
  1. Message @BotFather on Telegram → /newbot → get token
  2. Message @userinfobot → get your chat_id
  3. Add both to .env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

Usage:
    alerts = AlertSystem()
    alerts.trade_opened(signal, order)
    alerts.trade_closed(ticker, pnl, reason)
    alerts.daily_summary(metrics)
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from loguru import logger

from src.config import settings


class AlertSystem:
    """
    Sends Telegram notifications for key bot events.
    All sends are non-blocking — alerts fire in a background thread.

    If TELEGRAM_BOT_TOKEN is not set, all methods are no-ops.
    """

    def __init__(self):
        self.enabled = bool(
            settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID
        )
        if not self.enabled:
            logger.info(
                "AlertSystem: Telegram not configured — "
                "set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env to enable"
            )

    # ── Alert methods ─────────────────────────────────────────────────────────

    def trade_opened(self, signal, order) -> None:
        """Alert when a new trade is entered."""
        direction = signal.direction.value
        emoji     = "🟢" if direction == "LONG" else "🔴"

        msg = (
            f"{emoji} *Trade Opened*\n"
            f"`{signal.ticker}` {direction}\n\n"
            f"Entry:  `${order.entry_price:.2f}`\n"
            f"Stop:   `${order.stop_price:.2f}`\n"
            f"Target: `${order.target_price:.2f}`\n"
            f"Shares: `{order.shares}`\n"
            f"Size:   `${order.position_size_usd:,.0f}`\n"
            f"R:R:    `{signal.risk_reward}`\n"
            f"Score:  `{signal.score:.2f}`\n"
            f"RSI(2): `{signal.rsi:.1f}`\n\n"
            f"_Mode: {settings.TRADING_MODE}_"
        )
        self._send(msg)

    def trade_closed(
        self,
        ticker: str,
        pnl: float,
        exit_reason: str,
        entry_price: float = 0.0,
        exit_price: float  = 0.0,
        qty: int           = 0,
    ) -> None:
        """Alert when a trade closes."""
        emoji = "✅" if pnl >= 0 else "❌"
        sign  = "+" if pnl >= 0 else ""

        msg = (
            f"{emoji} *Trade Closed*\n"
            f"`{ticker}` — {exit_reason}\n\n"
            f"P&L:    `{sign}${pnl:,.2f}`\n"
        )
        if entry_price and exit_price:
            msg += (
                f"Entry:  `${entry_price:.2f}`\n"
                f"Exit:   `${exit_price:.2f}`\n"
            )
        if qty:
            msg += f"Shares: `{qty}`\n"

        self._send(msg)

    def daily_summary(
        self,
        daily_pnl: float,
        total_equity: float,
        n_trades: int,
        win_rate: float,
        open_positions: list[str],
    ) -> None:
        """Send end-of-day summary at 4pm ET."""
        emoji = "📈" if daily_pnl >= 0 else "📉"
        sign  = "+" if daily_pnl >= 0 else ""

        positions_str = (
            ", ".join(f"`{t}`" for t in open_positions)
            if open_positions else "_none_"
        )

        msg = (
            f"{emoji} *Daily Summary*\n"
            f"{datetime.now().strftime('%Y-%m-%d')}\n\n"
            f"Daily P&L:  `{sign}${daily_pnl:,.2f}`\n"
            f"Equity:     `${total_equity:,.2f}`\n"
            f"Trades:     `{n_trades}`\n"
            f"Win rate:   `{win_rate:.1%}`\n\n"
            f"Open positions: {positions_str}"
        )
        self._send(msg)

    def circuit_breaker(self, reason: str) -> None:
        """Alert when a circuit breaker halts trading."""
        msg = (
            f"🛑 *Circuit Breaker Triggered*\n\n"
            f"Reason: _{reason}_\n\n"
            f"Trading halted. Review and reset manually."
        )
        self._send(msg)

    def ml_retrained(self, accuracy: float, n_trades: int) -> None:
        """Alert when ML model retrains."""
        msg = (
            f"🤖 *ML Model Retrained*\n\n"
            f"Accuracy: `{accuracy:.1%}`\n"
            f"Trained on: `{n_trades}` trades\n"
            f"Date: {datetime.now().strftime('%Y-%m-%d')}"
        )
        self._send(msg)

    def bot_started(self, mode: str) -> None:
        """Alert when the bot starts."""
        emoji = "🚀" if mode == "paper" else "⚡"
        msg   = (
            f"{emoji} *Bot Started*\n\n"
            f"Mode: `{mode.upper()}`\n"
            f"Capital: `${settings.TOTAL_CAPITAL:,.0f}`\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M ET')}"
        )
        self._send(msg)

    def bot_stopped(self) -> None:
        """Alert when the bot stops."""
        self._send("⏹️ *Bot Stopped*\n\nTrading session ended.")

    def error_alert(self, message: str) -> None:
        """Alert for unexpected errors."""
        self._send(f"⚠️ *Bot Error*\n\n`{message[:300]}`")

    def custom(self, message: str) -> None:
        """Send a custom message."""
        self._send(message)

    # ── Private ───────────────────────────────────────────────────────────────

    def _send(self, message: str) -> None:
        """Fire-and-forget send in a background thread."""
        if not self.enabled:
            return
        thread = threading.Thread(
            target=self._send_sync,
            args=(message,),
            daemon=True,
        )
        thread.start()

    def _send_sync(self, message: str) -> None:
        """Synchronous send — runs in background thread."""
        try:
            import urllib.request
            import json

            url  = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
            data = json.dumps({
                "chat_id":    settings.TELEGRAM_CHAT_ID,
                "text":       message,
                "parse_mode": "Markdown",
            }).encode("utf-8")

            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    logger.warning(f"Telegram send failed: {resp.status}")

        except Exception as e:
            logger.warning(f"Telegram alert failed: {e}")
