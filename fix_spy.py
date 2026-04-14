import yfinance as yf
from src.data.store import DataStore

store = DataStore()
print("Downloading SPY data...")
spy = yf.download("SPY", period="5y", interval="1d", auto_adjust=True)

# Flatten multi-level columns if needed
if hasattr(spy.columns, "levels"):
    spy.columns = [c[0].lower() for c in spy.columns]
else:
    spy.columns = [c.lower() for c in spy.columns]

spy.index.name = "datetime"
print(f"Columns: {list(spy.columns)}")
store.save_bars("SPY", spy)
print(f"SPY loaded: {len(spy)} bars")
print("Done!")