"""
Nuclear fix for live_trader.py - rewrites start() with correct indentation.
Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_live_trader.py
"""
import re

path = r"C:\IBKR Bot\trading-bot\src\execution\live_trader.py"

with open(path, "r", encoding="utf-8") as f:
    content = f.read()

CORRECT_START = '''    def start(self) -> None:
        """Start the live trading loop. Blocks until stopped."""
        self._init_components()

        # ALWAYS connect to IBKR for real-time market data, even in paper mode.
        # Paper TWS (port 7497) provides live data for free.
        # Only order execution is simulated locally in paper mode.
        logger.info(
            f"Connecting to IBKR for market data | "
            f"{'LIVE orders' if self.is_live else 'PAPER - orders simulated locally'}"
        )
        ibkr_connected = self._connect_ibkr()

        if ibkr_connected:
            logger.success(
                f"IBKR connected | real-time TWS data | "
                f"orders={'LIVE' if self.is_live else 'PAPER simulated'}"
            )
            self._intraday.use_ibkr = True
            self._intraday.client   = self._client
        else:
            if self.is_live:
                logger.error("IBKR connection failed - cannot run live without connection")
                return
            else:
                logger.warning(
                    "IBKR not connected - falling back to yfinance.\\n"
                    "  Start TWS on port 7497 and enable API connections."
                )

        # Warm up 5-min bar history before entering loop
        logger.info("Warming up intraday data (5-min bars)...")
        self._intraday.warmup(days=5)

        if not self._intraday.is_ready(min_bars=25):
            logger.warning(
                "Not enough intraday bars loaded - "
                "signals may be unreliable until more bars accumulate"
            )

        self._running = True
        self._alerts.bot_started(self.mode)
        logger.success(f"LiveTrader started | mode={self.mode}")

        self._run_loop()

'''

pattern = r'    def start\(self\) -> None:.*?(?=    def stop\(self\))'
match = re.search(pattern, content, re.DOTALL)

if match:
    print(f"Found start() at chars {match.start()}-{match.end()}")
    new_content = content[:match.start()] + CORRECT_START + content[match.end():]
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)
    print("SUCCESS: live_trader.py fixed!")
    print("Now run: python scripts/run_live.py --paper")
else:
    print("ERROR: Could not find start() method.")
    lines = content.split('\n')
    for i, line in enumerate(lines[55:100], 56):
        print(f"{i:3}: {repr(line)}")
