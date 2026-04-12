# Algorithmic Trading Bot

Automated intraday equity trading system for Interactive Brokers.  
Targets large-cap tech (TSLA, NVDA, META, GOOGL, AMD, MSFT, AMZN, AAPL) with a  
self-improving three-layer signal architecture.

---

## Architecture

```
Data pipeline  â†’  Signal engine  â†’  Risk manager  â†’  IBKR execution
     â†‘                 â†‘                                    â†“
Feature store    Meta-labeler (ML)              Trade log (SQLite)
     â†‘                 â†‘                                    â†“
  yfinance +     Weekly retrain            Dashboard + Telegram alerts
    IBKR            (HRP)
```

### Signal layers
| Layer | Indicator | Role | Weight |
|-------|-----------|------|--------|
| Primary | RSI(2) extreme reversal | Long below 10, short above 90 | 50% |
| Secondary | Bollinger Band touch | Confirms mean-reversion setup | 30% |
| Tertiary | EMA 9Ã—20 cross | Momentum direction filter | 20% |

A **meta-labeler** (RandomForest, retrained weekly on live trade outcomes)  
vetos signals that historically fail â€” the bot improves its own accuracy over time.

---

## Quick start

```bash
# 1. Clone and enter
git clone https://github.com/YOUR_USERNAME/trading-bot.git
cd trading-bot

# 2. First-time setup (creates .env, directories, installs deps)
make setup

# 3. Edit .env with your values
nano .env          # or code .env / notepad .env on Windows

# 4. Install IBKR API (see Â§ IBKR Setup below)

# 5. Run backtest
make backtest      # HTML report in reports/

# 6. Paper trade
make paper         # connect TWS on port 7497

# 7. Monitor
make dashboard     # http://localhost:5000
```

---

## Windows Quick Start

No WSL required. Use the provided batch files:

```batch
REM 1. First-time setup (installs dependencies, creates .env)
setup.bat

REM 2. Edit .env with your values
notepad .env

REM 3. Install IBKR API (see IBKR Setup section below)

REM 4. Run backtest
run.bat backtest

REM 5. Paper trade
run.bat paper

REM 6. Open dashboard
run.bat dashboard

REM 7. Diagnose your paper results
run.bat diagnose
```

All `make` commands have a `run.bat` equivalent. Run `run.bat help` to see the full list.

---

## IBKR Setup

The IBKR Python API is **not on PyPI** â€” you must install it manually.

1. Download TWS API from https://interactivebrokers.github.io/
2. Extract the zip
3. Navigate to `TWS_API/source/pythonclient/`
4. Run: `pip install .`
5. Open Trader Workstation (TWS)
6. Enable API: `Edit â†’ Global Configuration â†’ API â†’ Settings`
   - âœ… Enable ActiveX and Socket Clients
   - âœ… Allow connections from localhost only
   - Socket port: **7497** (paper) or **7496** (live)
7. Set `IBKR_PORT=7497` in your `.env`

---

## Project structure

```
trading-bot/
â”œâ”€â”€ config/
â”‚   â””â”€â”€ config.yaml          # all tunable parameters
â”œâ”€â”€ data/
â”‚   â”œâ”€â”€ raw/                 # downloaded OHLCV bars
â”‚   â”œâ”€â”€ processed/           # feature-engineered data
â”‚   â””â”€â”€ trading_bot.db       # SQLite: trades, positions, equity curve
â”œâ”€â”€ logs/                    # rotating daily log files
â”œâ”€â”€ models/                  # versioned ML meta-labeler models
â”œâ”€â”€ reports/                 # backtest HTML reports
â”œâ”€â”€ scripts/
â”‚   â”œâ”€â”€ run_backtest.py      # make backtest
â”‚   â”œâ”€â”€ run_live.py          # make paper / make live
â”‚   â”œâ”€â”€ run_dashboard.py     # make dashboard
â”‚   â””â”€â”€ run_retrain.py       # make retrain
â”œâ”€â”€ src/
â”‚   â”œâ”€â”€ config.py            # settings singleton
â”‚   â”œâ”€â”€ logger.py            # loguru setup
â”‚   â”œâ”€â”€ data/                # Phase 2: DataLoader, features
â”‚   â”œâ”€â”€ strategy/            # Phase 3: SignalEngine, meta-labeler
â”‚   â”œâ”€â”€ risk/                # Phase 5: RiskManager, Kelly, circuit breakers
â”‚   â”œâ”€â”€ backtest/            # Phase 4: Backtester, purged CV, HTML report
â”‚   â”œâ”€â”€ ml/                  # Phase 6: SelfLearner, HRP optimiser
â”‚   â”œâ”€â”€ execution/           # Phase 7: LiveTrader, IBKR order management
â”‚   â””â”€â”€ dashboard/           # Phase 8: Flask app, Telegram alerts
â”œâ”€â”€ tests/                   # pytest test suite
â”œâ”€â”€ .env.template            # copy to .env and fill in values
â”œâ”€â”€ .gitignore
â”œâ”€â”€ Makefile
â”œâ”€â”€ README.md
â””â”€â”€ requirements.txt
```

---

## Make commands

| Command | Description |
|---------|-------------|
| `make setup` | First-time setup: .env + dirs + pip install |
| `make backtest` | Walk-forward backtest across full universe |
| `make backtest-custom START=2022-01-01 END=2024-01-01` | Custom date range |
| `make paper` | Start paper trading bot |
| `make live` | Start live trading (requires confirmation) |
| `make dashboard` | Launch monitoring dashboard |
| `make retrain` | Manually trigger ML retraining |
| `make test` | Run pytest with coverage |
| `make check-config` | Validate .env and config.yaml |
| `make clean` | Remove Python cache files |

---

## Build phases

| Phase | Module | Status |
|-------|--------|--------|
| 1 | Scaffold (this file) | âœ… |
| 2 | Data pipeline (DataLoader, fractional diff, features) | â¬œ |
| 3 | Signal engine (RSI/BB/EMA + meta-labeling) | â¬œ |
| 4 | Walk-forward backtester (purged CV, HTML report) | â¬œ |
| 5 | Risk manager (Kelly, ATR stops, circuit breakers) | â¬œ |
| 6 | Self-improving ML layer (RF + HRP weekly retrain) | â¬œ |
| 7 | IBKR live execution (bracket orders, state machine) | â¬œ |
| 8 | Dashboard + Telegram alerts | â¬œ |

---

## Book references

| Book | Applied in |
|------|-----------|
| LÃ³pez de Prado â€” *Advances in Financial Machine Learning* | Fractional diff, meta-labeling, purged CV, CPCV |
| LÃ³pez de Prado â€” *Machine Learning for Asset Managers* | HRP, feature importance, clustering |
| Ernest Chan â€” *Algorithmic Trading* | Mean reversion signals, backtesting discipline |
| Ernest Chan â€” *Quantitative Trading* | Kelly criterion, strategy selection |
| Nassim Taleb â€” *The Black Swan* | Fat-tail position caps, circuit breakers |
| Larry Harris â€” *Trading and Exchanges* | Avoid open/close 15min, execution microstructure |
| Antti Ilmanen â€” *Expected Returns* | Risk premia factor exposure |
| Mark Douglas â€” *Trading in the Zone* | Sizing discipline, half-Kelly rationale |

---

## Safety

- **Paper mode by default.** Live orders require `TRADING_MODE=live` in `.env` AND `--live` flag AND terminal confirmation.
- **Daily drawdown circuit breaker.** Bot halts if daily P&L drops below `-3%` of capital.
- **Max 3 simultaneous positions.** Never more than 20% of capital in open positions.
- **Hard position cap.** No single ticker ever exceeds `$2,500` regardless of Kelly.
- **Microstructure guard.** No orders in first or last 15 minutes of each session.

---

## Safety

- **Paper mode by default.** Live orders require `TRADING_MODE=live` in `.env` AND `--live` flag AND terminal confirmation.
- **Daily drawdown circuit breaker.** Bot halts if daily P&L drops below `-3%` of capital.
- **Max 3 simultaneous positions.** Never more than 20% of capital in open positions.
- **Hard position cap.** No single ticker ever exceeds `$2,500` regardless of Kelly.
- **Microstructure guard.** No orders in first or last 15 minutes of each session.

---

## QuantConnect Research Layer (Hybrid Approach)

The `quantconnect/` directory contains a mirror of this strategy as a
QuantConnect Cloud algorithm — used for backtesting parameter variants
against 20 years of survivorship-bias-free data before applying changes
to the live bot.

### Diagnostic First

Before researching new parameters, run the diagnostic tool to see where
your current live/paper results are leaking edge:

```bash
# Mac / Linux
python scripts/diagnose_paper.py --html

# Windows
run.bat diagnose-html
```

This produces a report breaking down performance by ticker, hour of day,
signal score bucket, and stop-out rate — pointing exactly where to focus.

### Research Workflow

1. `run.bat diagnose` — identify what is broken
2. Open `quantconnect/parameter_variants.py` — pick a matching variant
3. Paste `quantconnect/rsi_mean_reversion.py` into QC Cloud (free account)
4. Edit parameters at the top to match your chosen variant
5. Run backtest in QC — validate Sharpe, CAGR, MaxDD over 5+ years
6. `python scripts/sync_qc_params.py --variant <name> --dry-run` — review diff
7. `python scripts/sync_qc_params.py --variant <name>` — apply to config.yaml
8. `run.bat backtest` — verify locally
9. `run.bat paper` (2 weeks minimum) — paper trade the new params
10. `run.bat live` — promote to live

### Why This Matters

Your local backtest uses yfinance data (5 years, 10 hardcoded tickers).
QC's backtest uses AlgoSeek institutional data (20 years, all tickers,
survivorship-bias-free). A strategy that looks good locally but fails
in QC almost certainly has data snooping bias — the QC result is the
higher-trust signal.

---

## Disclaimer

This software is for educational purposes. Trading equities involves significant  
financial risk. Past backtested performance does not guarantee future results.

