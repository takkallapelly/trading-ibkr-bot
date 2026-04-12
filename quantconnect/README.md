# QuantConnect Research Layer

This directory contains the QuantConnect Cloud version of the strategy — used
for research, parameter validation, and options exploration.

## Quick Start (5 minutes)

### 1. Create a free QuantConnect account
Go to [quantconnect.com](https://quantconnect.com) → Sign Up (no credit card needed).

### 2. Create a new algorithm
- Click **Algorithm Lab** in the left sidebar
- Click **+ New Algorithm**
- Select **Python**
- Delete the default code

### 3. Paste the strategy
Copy the entire contents of `rsi_mean_reversion.py` and paste it into the editor.

### 4. Run a backtest
Click **Backtest** (top right). Wait ~2 minutes. Review:
- Sharpe ratio (target > 1.0)
- CAGR (target > 15%)
- Max Drawdown (target < 20%)
- Compare to your local backtest HTML reports in `reports/`

### 5. Test parameter variants
Open `parameter_variants.py` to see 6 pre-built configs.

To test `wider_stop`, change this line at the top of `rsi_mean_reversion.py`:

```python
STOP_ATR_MULT  = 1.5   # was 1.0
```

Re-run the backtest. If Sharpe improves, apply to your live bot:

```bash
# Mac / Linux
python scripts/sync_qc_params.py --variant wider_stop

# Windows
run.bat sync-params
python scripts\sync_qc_params.py --variant wider_stop
```

## Parameter Variants

| Variant | What It Tests | Key Change |
|---|---|---|
| `baseline` | Exact live bot match — use as benchmark | — |
| `wider_stop` | Fix high stop-out rate | stop 1.0→1.5×ATR |
| `tighter_signal` | Quality over quantity | min_score 0.60→0.75 |
| `wider_rsi` | More signals, lower bar | RSI 10/90→15/85 |
| `larger_target` | Let winners run longer | TP 2.0→3.0×ATR |
| `conservative` | Max precision, fewest trades | All tightened |

## Research → Live Workflow

```
1. python scripts/diagnose_paper.py     ← find where edge is leaking
        ↓
2. Pick a matching variant from parameter_variants.py
        ↓
3. Paste rsi_mean_reversion.py into QC Cloud, edit parameters
        ↓
4. Run backtest in QC → validate Sharpe, CAGR, MaxDD over 5+ years
        ↓
5. python scripts/sync_qc_params.py --variant <name> --dry-run
        ↓
6. Review config.yaml diff carefully
        ↓
7. python scripts/sync_qc_params.py --variant <name>
        ↓
8. python scripts/run_backtest.py       ← verify locally with your data
        ↓
9. run.bat paper  (2 weeks minimum)     ← paper trade the new params
        ↓
10. run.bat live                        ← promote to live
```

## Rules

- Never apply QC results directly to live — always paper trade first
- Change one parameter at a time — isolate variables
- A higher Sharpe with 35%+ drawdown is NOT better
- If QC says a strategy works but local backtest doesn't agree, trust neither — investigate the data difference
