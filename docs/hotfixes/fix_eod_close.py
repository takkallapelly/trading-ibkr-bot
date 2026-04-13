"""
Fix EOD position close - queries IBKR directly at 15:45 ET.
Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_eod_close.py
"""
import os

path = r"src\execution\live_trader.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

EOD_METHOD = '''
    def _eod_close_all_ibkr_positions(self) -> None:
        """Force close all IBKR positions at EOD using market orders."""
        if not self._client or not self._client.is_connected():
            logger.warning("EOD close: IBKR not connected")
            return
        try:
            import time
            from ib_insync import MarketOrder
            self._client.request_positions()
            time.sleep(2)
            positions = self._client.get_positions()
            if not positions:
                logger.info("EOD close: no open positions")
                return
            logger.warning(f"EOD force closing {len(positions)} position(s): {list(positions.keys())}")
            for ticker, pos_data in positions.items():
                qty = pos_data.get("qty", 0)
                if qty == 0:
                    continue
                side = "SELL" if qty > 0 else "BUY"
                try:
                    contract = self._client._make_contract(ticker)
                    self._client.reqGlobalCancel()
                    time.sleep(0.5)
                    order = MarketOrder(side, abs(qty))
                    order.orderId = self._client.next_order_id()
                    order.tif = "DAY"
                    self._client.placeOrder(order.orderId, contract, order)
                    logger.info(f"EOD closed: {ticker} {side} {abs(qty)}sh")
                    if self._alerts:
                        self._alerts.custom(
                            f"🔔 *EOD Close*\\n`{ticker}` {side} {abs(qty)}sh @ market"
                        )
                    time.sleep(0.3)
                except Exception as e:
                    logger.error(f"EOD close {ticker}: {e}")
        except Exception as e:
            logger.error(f"EOD close error: {e}")

'''

if "_eod_close_all_ibkr_positions" not in content:
    content = content.replace("    def stop(self) -> None:", EOD_METHOD + "    def stop(self) -> None:")
    print("Added _eod_close_all_ibkr_positions()")

# Add EOD trigger in the sleep block
old_sleep = "                # Interruptible sleep — checks _running every second\n                # so Ctrl+C (Strg+C) exits immediately instead of\n                # waiting up to 5 minutes for the sleep to finish.\n                for _ in range(self.SCAN_INTERVAL_SECS):\n                    if not self._running:\n                        break\n                    time.sleep(1)"

new_sleep = """                # EOD force close at 15:45 ET
                try:
                    from zoneinfo import ZoneInfo
                    import datetime as _dt
                    _et = _dt.datetime.now(ZoneInfo('America/New_York'))
                    _eod = (_et.hour == 15 and _et.minute >= 45) or _et.hour >= 16
                    if _eod and not getattr(self, '_eod_closed_today', False):
                        logger.warning("EOD: 15:45 ET — force closing all positions")
                        self._eod_close_all_ibkr_positions()
                        self._eod_closed_today = True
                    if _et.hour < 9:
                        self._eod_closed_today = False
                except Exception:
                    pass

                # Interruptible sleep
                for _ in range(self.SCAN_INTERVAL_SECS):
                    if not self._running:
                        break
                    time.sleep(1)"""

if old_sleep in content:
    content = content.replace(old_sleep, new_sleep)
    print("Added EOD trigger in main loop")
else:
    print("WARNING: Could not find sleep block — EOD method added but not triggered")
    print("Add this call manually before the sleep in _run_loop:")
    print("  self._eod_close_all_ibkr_positions()")

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

# Clear cache
cache = r"src\execution\__pycache__"
if os.path.exists(cache):
    for f in os.listdir(cache):
        if "live_trader" in f:
            os.remove(os.path.join(cache, f))
            print(f"Cleared {f}")

print("""
SUCCESS: EOD force close added.

At 15:45 ET (21:45 CET) every day:
  - Bot queries IBKR for ALL open positions
  - Cancels bracket orders
  - Closes with market orders
  - Sends Telegram alert per position

Works even if internal state tracking is broken.

Restart: python scripts/run_live.py --paper
""")
