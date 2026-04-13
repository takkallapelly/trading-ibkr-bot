import sqlite3

conn = sqlite3.connect('data/trading_bot.db')
conn.row_factory = sqlite3.Row
rows = conn.execute('''
    SELECT id, ticker, side, entry_time, exit_time, 
           entry_price, exit_price, pnl, exit_reason 
    FROM trades 
    ORDER BY entry_time
''').fetchall()

print(f"Total trades: {len(rows)}")
for r in rows:
    print(dict(r))

conn.close()