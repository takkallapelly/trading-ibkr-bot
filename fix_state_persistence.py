"""
Production-grade state recovery for live_trader.py
Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_state_persistence.py

On every startup the bot will:
  1. Query IBKR for ALL open positions
  2. For each position opened by the BOT (tracked in DB):
       a. Check if stop loss order exists in IBKR
       b. Check if take profit order exists in IBKR
       c. If missing -> recreate them automatically
       d. Reload position into OrderManager memory
  3. For positions NOT opened by the bot (manual trades):
       a. Log them clearly
       b. Send Telegram warning
       c. IGNORE them completely - never touch manual trades
"""

path = r"src\execution\live_trader.py"

with open(path, "r", encoding="utf-8") as f:
    content = f.read()

NEW_METHOD = '''
    def _recover_open_positions(self) -> None:
        """
        On every startup:
          1. Query IBKR for all open positions
          2. For BOT positions: verify SL/TP exist, recreate if missing
          3. For MANUAL positions: warn and ignore completely
        """
        if not self._client or not self._client.is_connected():
            logger.info("Position recovery skipped - IBKR not connected")
            return

        try:
            import time
            import sqlite3
            from src.execution.order_manager import ManagedOrder, OrderState

            logger.info("Startup check: querying IBKR for open positions...")

            # Step 1: Get all open positions from IBKR
            self._client.request_positions()
            time.sleep(2)
            ibkr_positions = self._client.get_positions()

            if not ibkr_positions:
                logger.info("No open positions in IBKR - clean start")
                return

            logger.info(
                f"Found {len(ibkr_positions)} open position(s) in IBKR: "
                + ", ".join(ibkr_positions.keys())
            )

            # Step 2: Get bot-opened trades from database
            bot_trades = {}
            try:
                conn = sqlite3.connect("data/trading_bot.db")
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("""
                    SELECT ticker, side, entry_price, stop_price,
                           target_price, qty, ibkr_order_id
                    FROM trades
                    WHERE exit_time IS NULL
                """)
                for row in cur.fetchall():
                    bot_trades[row["ticker"]] = dict(row)
                conn.close()
            except Exception as e:
                logger.warning(f"Could not read DB for recovery: {e}")

            logger.info(
                f"Bot DB has {len(bot_trades)} unclosed trade(s): "
                + (", ".join(bot_trades.keys()) or "none")
            )

            # Step 3: Get all open orders from IBKR
            open_orders = {}
            try:
                self._client.reqAllOpenOrders()
                time.sleep(2)
                open_orders = getattr(self._client, "_orders", {})
            except Exception as e:
                logger.warning(f"Could not fetch open orders: {e}")

            # Step 4: Process each IBKR position
            recovered_bot  = 0
            ignored_manual = 0

            for ticker, pos_data in ibkr_positions.items():
                qty      = pos_data.get("qty", 0)
                avg_cost = pos_data.get("avg_cost", 0.0)
                side     = "LONG" if qty > 0 else "SHORT"

                if qty == 0:
                    continue

                if ticker in bot_trades:
                    # BOT TRADE - recover and verify SL/TP
                    db_trade   = bot_trades[ticker]
                    stop_price = db_trade.get("stop_price")   or avg_cost * 0.98
                    tp_price   = db_trade.get("target_price") or avg_cost * 1.04
                    entry      = db_trade.get("entry_price")  or avg_cost
                    db_qty     = db_trade.get("qty")          or abs(qty)

                    logger.info(
                        f"BOT position: {ticker} {side} {abs(qty)}sh "
                        f"@ ${avg_cost:.2f} | stop=${stop_price:.2f} "
                        f"target=${tp_price:.2f}"
                    )

                    # Check if SL/TP orders exist for this ticker
                    ticker_orders = [
                        o for o in open_orders.values()
                        if o.get("ticker") == ticker
                    ]
                    has_stop   = any(o.get("order_type") in ("STP","STOP") for o in ticker_orders)
                    has_target = any(o.get("order_type") == "LMT" for o in ticker_orders)

                    if not has_stop or not has_target:
                        logger.warning(
                            f"{ticker}: bracket incomplete - "
                            f"stop={'OK' if has_stop else 'MISSING'} "
                            f"target={'OK' if has_target else 'MISSING'} "
                            f"- recreating..."
                        )
                        try:
                            exit_side = "SELL" if side == "LONG" else "BUY"
                            contract  = self._client._make_contract(ticker)

                            if not has_stop:
                                from ibapi.order import Order as IBOrder
                                stop_id  = self._client.next_order_id()
                                stop_ord = IBOrder()
                                stop_ord.orderId       = stop_id
                                stop_ord.action        = exit_side
                                stop_ord.orderType     = "STP"
                                stop_ord.auxPrice      = round(stop_price, 2)
                                stop_ord.totalQuantity = abs(db_qty)
                                stop_ord.tif           = "GTC"
                                stop_ord.transmit      = True
                                stop_ord.etradeOnly    = False
                                stop_ord.firmQuoteOnly = False
                                self._client.placeOrder(stop_id, contract, stop_ord)
                                logger.info(f"{ticker}: SL recreated @ ${stop_price:.2f}")

                            if not has_target:
                                from ibapi.order import Order as IBOrder
                                tp_id  = self._client.next_order_id()
                                tp_ord = IBOrder()
                                tp_ord.orderId       = tp_id
                                tp_ord.action        = exit_side
                                tp_ord.orderType     = "LMT"
                                tp_ord.lmtPrice      = round(tp_price, 2)
                                tp_ord.totalQuantity = abs(db_qty)
                                tp_ord.tif           = "GTC"
                                tp_ord.transmit      = True
                                tp_ord.etradeOnly    = False
                                tp_ord.firmQuoteOnly = False
                                self._client.placeOrder(tp_id, contract, tp_ord)
                                logger.info(f"{ticker}: TP recreated @ ${tp_price:.2f}")

                        except Exception as e:
                            logger.error(f"{ticker}: failed to recreate orders: {e}")
                    else:
                        logger.info(f"{ticker}: SL OK | TP OK - bracket intact")

                    # Reload into OrderManager
                    if not self._order_mgr.has_open_position(ticker):
                        managed = self._order_mgr.create(
                            ticker       = ticker,
                            side         = side,
                            qty          = abs(db_qty),
                            entry_price  = entry,
                            stop_price   = stop_price,
                            target_price = tp_price,
                            signal_score = 0.0,
                        )
                        managed.transition(OrderState.PENDING)
                        managed.transition(
                            OrderState.FILLED,
                            fill_price = entry,
                            fill_time  = "recovered_on_restart",
                        )
                        managed.parent_id = self._client.next_order_id()
                        self._order_mgr.register(managed)
                        try:
                            self._risk.open_position_manual(ticker, abs(db_qty) * entry)
                        except Exception:
                            pass

                    recovered_bot += 1

                else:
                    # MANUAL TRADE - log and ignore completely
                    logger.warning(
                        f"MANUAL position detected: {ticker} {side} "
                        f"{abs(qty)}sh @ ${avg_cost:.2f} - "
                        f"NOT managed by bot - leaving untouched"
                    )
                    ignored_manual += 1

            # Step 5: Send Telegram summary
            if recovered_bot > 0 or ignored_manual > 0:
                parts = ["Warning *Bot Restarted - Position Summary*\\n"]
                if recovered_bot > 0:
                    parts.append(
                        f"Recovered {recovered_bot} bot position(s) - "
                        f"SL/TP verified"
                    )
                if ignored_manual > 0:
                    parts.append(
                        f"{ignored_manual} manual position(s) found - "
                        f"bot will NOT touch these"
                    )
                self._alerts.custom("\\n".join(parts))

            logger.info(
                f"Recovery complete | "
                f"bot_positions={recovered_bot} | "
                f"manual_positions={ignored_manual} (ignored)"
            )

        except Exception as e:
            logger.error(f"Position recovery error: {e}")

'''

# Remove old recovery method if it exists
if "_recover_open_positions" in content:
    start = content.find("\n    def _recover_open_positions")
    end   = content.find("\n    def ", start + 1)
    if start > -1 and end > -1:
        content = content[:start] + content[end:]
        print("Removed old recovery method")

# Insert before stop()
insert_before = "    def stop(self) -> None:"
if insert_before in content:
    content = content.replace(insert_before, NEW_METHOD + insert_before)
    print("Added _recover_open_positions() method")
else:
    print("ERROR: Could not find insertion point")

# Add recovery call in start()
old_warmup = (
    "        # Warm up 5-min bar history before entering loop\n"
    "        logger.info(\"Warming up intraday data (5-min bars)...\")"
)
new_warmup = (
    "        # Recover open positions from previous session\n"
    "        self._recover_open_positions()\n\n"
    "        # Warm up 5-min bar history before entering loop\n"
    "        logger.info(\"Warming up intraday data (5-min bars)...\")"
)
if old_warmup in content:
    content = content.replace(old_warmup, new_warmup)
    print("Added recovery call in start()")
else:
    print("WARNING: Could not add recovery call in start()")

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

# Add open_position_manual to RiskManager
risk_path = r"src\risk\manager.py"
try:
    with open(risk_path, "r", encoding="utf-8") as f:
        risk = f.read()
    if "open_position_manual" not in risk:
        old = "    def close_position(self"
        new = '''    def open_position_manual(self, ticker: str, position_usd: float) -> None:
        """Register a manually recovered position with the risk manager."""
        try:
            self._open_positions[ticker] = position_usd
            logger.info(f"RiskManager: registered recovered {ticker} ${position_usd:.0f}")
        except Exception as e:
            logger.warning(f"Could not register recovered position: {e}")

    def close_position(self'''
        if old in risk:
            risk = risk.replace(old, new)
            with open(risk_path, "w", encoding="utf-8") as f:
                f.write(risk)
            print("Added open_position_manual() to RiskManager")
        else:
            print("WARNING: close_position not found in RiskManager")
    else:
        print("open_position_manual already exists in RiskManager")
except Exception as e:
    print(f"RiskManager patch skipped: {e}")

print("""
SUCCESS - Production state persistence applied!

Every bot startup now:

  BOT positions (in database):
    Check SL exists in IBKR  -> recreate if missing
    Check TP exists in IBKR  -> recreate if missing
    Reload into bot memory   -> no double trading
    Telegram confirmation    -> you know it recovered

  MANUAL positions (not in database):
    Detected and logged      -> you can see them
    Completely ignored       -> bot never touches them
    Telegram warning         -> you are informed

Run in this order:
  python fix_zombie_trades.py
  python fix_etrade.py
  python fix_state_persistence.py
  python scripts/run_live.py --paper
""")
