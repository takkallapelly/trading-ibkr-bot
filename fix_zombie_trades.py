"""
Check and fix zombie trades stuck as open in the database.
Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_zombie_trades.py
"""
import sqlite3
from datetime import datetime

db_path = r"data\trading_bot.db"

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Show all tables first
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print(f"Tables in DB: {tables}")

# Find open trades
try:
    cur.execute("SELECT * FROM trades WHERE exit_time IS NULL")
    open_trades = cur.fetchall()

    if not open_trades:
        print("\n✓ No zombie trades found — database is clean")
    else:
        print(f"\n⚠ Found {len(open_trades)} open trade(s) stuck in DB:")
        print("-" * 80)
        for t in open_trades:
            d = dict(t)
            print(f"  ID={d.get('id')} | {d.get('ticker')} {d.get('side')} | "
                  f"entry=${d.get('entry_price', 0):.2f} | "
                  f"qty={d.get('qty')} | "
                  f"entry_time={d.get('entry_time')}")
        print("-" * 80)

        # Close them as cancelled
        print("\nClosing zombie trades as CANCELLED...")
        now = datetime.utcnow().isoformat()
        cur.execute("""
            UPDATE trades
            SET exit_time   = ?,
                exit_price  = entry_price,
                pnl         = 0,
                exit_reason = 'CANCELLED_ON_RESTART'
            WHERE exit_time IS NULL
        """, (now,))
        conn.commit()
        print(f"✓ Closed {cur.rowcount} zombie trade(s) as CANCELLED (pnl=$0)")

except Exception as e:
    print(f"Error: {e}")
    print("The trades table may have a different schema.")
    # Show schema
    try:
        cur.execute("PRAGMA table_info(trades)")
        cols = cur.fetchall()
        print("Columns in trades table:")
        for col in cols:
            print(f"  {col}")
    except:
        pass

conn.close()
print("\nDone. Restart the bot: python scripts/run_live.py --paper")
