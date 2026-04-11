"""
Add debug logging to _process_signal to find why signals are silently blocked.
Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_signal_debug.py
"""
path = r"src\execution\live_trader.py"

with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old_process = '''    def _process_signal(self, signal) -> None:
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

        self._place_order(order)'''

new_process = '''    def _process_signal(self, signal) -> None:
        """Process one signal through risk manager → order placement."""
        ticker = signal.ticker

        # ── Check 1: already have position ───────────────────────────────────
        if self._order_mgr.has_open_position(ticker):
            logger.info(f"{ticker}: SKIPPED — position already open in OrderManager")
            return

        # ── Check 2: risk manager approval ───────────────────────────────────
        daily_pnl = self._store.daily_pnl_today()
        open_pos  = self._order_mgr.open_positions_for_risk()
        n_open    = len(self._order_mgr.open_orders())

        logger.info(
            f"{ticker}: processing signal | "
            f"open_positions={n_open} | daily_pnl=${daily_pnl:.2f}"
        )

        order = self._risk.approve_entry(
            signal,
            daily_pnl=daily_pnl,
            open_positions=open_pos,
        )

        if not order.approved:
            logger.info(f"{ticker}: REJECTED by risk — {order.rejection_reason}")
            return

        logger.info(f"{ticker}: APPROVED by risk — placing order")
        self._place_order(order)'''

if old_process in content:
    content = content.replace(old_process, new_process)
    print("SUCCESS: Added debug logging to _process_signal")
else:
    print("Pattern not found — trying fuzzy match...")
    if "def _process_signal" in content:
        # Find and show current implementation
        start = content.find("    def _process_signal")
        end = content.find("\n    def ", start + 1)
        print("Current _process_signal:")
        print(content[start:end])
    else:
        print("ERROR: _process_signal not found")

# Clear pyc cache
import os
cache = r"src\execution\__pycache__"
if os.path.exists(cache):
    for f in os.listdir(cache):
        if "live_trader" in f:
            os.remove(os.path.join(cache, f))

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("Restart bot — next signals will show exact reason for blocking")
