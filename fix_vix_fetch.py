"""Fix VIX yfinance parameters."""
path = r"src\data\vix.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()
content = content.replace('period="1d", interval="5m"', 'period="5d", interval="1h"')
with open(path, "w", encoding="utf-8") as f:
    f.write(content)
with open(path) as f:
    check = f.read()
if '5d' in check:
    print("SUCCESS: VIX now uses 5d/1h bars — more reliable data")
else:
    print("ERROR: fix did not apply")
