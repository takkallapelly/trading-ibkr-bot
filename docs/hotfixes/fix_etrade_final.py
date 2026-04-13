"""Fix IBKR error 10268 — run from C:\\IBKR Bot\\trading-bot"""
import os
path = r"src\execution\ibkr_client.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

patch = '''
# Monkey-patch ibapi Order: etradeOnly="" = not sent in wire protocol
try:
    from ibapi.order import Order as _IBOrder
    _orig = _IBOrder.__init__
    def _new(self, *a, **k):
        _orig(self, *a, **k)
        self.etradeOnly    = ""
        self.firmQuoteOnly = ""
    _IBOrder.__init__ = _new
except Exception:
    pass
'''

if "Monkey-patch ibapi Order" not in content:
    insert = "from loguru import logger\n"
    if insert in content:
        content = content.replace(insert, insert + patch, 1)
        print("Added monkey-patch")
    else:
        content = patch + content
        print("Added monkey-patch at top")
else:
    print("Already present")

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

cache = r"src\execution\__pycache__"
if os.path.exists(cache):
    for f in os.listdir(cache):
        if "ibkr_client" in f:
            os.remove(os.path.join(cache, f))
            print(f"Cleared {f}")

print("SUCCESS — restart: python scripts/run_live.py --paper")
