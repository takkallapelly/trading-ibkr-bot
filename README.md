# Algorithmic Trading Bot

Automated intraday equity trading system for Interactive Brokers.  
Targets large-cap tech (TSLA, NVDA, META, GOOGL, AMD, MSFT, AMZN, AAPL) with a  
self-improving three-layer signal architecture.

---

## Architecture

```
Data pipeline  →  Signal engine  →  Risk manager  →  IBKR execution
     ↑                 ↑                                    ↓
Feature store    Meta-labeler (ML)              Trade log (SQLite)
     ↑                 ↑                                    ↓
  yfinance +     Weekly retrain            Dashboard + Telegram alerts
    IBKR            (HRP)
```

### Signal layers
| Layer | Indicator | Role | Weight |
|-------|-----------|------|--------|
| Primary | RSI(2) extreme reversal | Long below 10, short above 90 | 50% |
| Secondary | Bollinger Band touch | Confirms mean-reversion setup | 30% |
| Tertiary | EMA 9×20 cross | Momentum direction filter | 20% |

A **meta-labeler** (RandomForest, retrained weekly on live trade outcomes)  
vetos signals that historically fail — the bot improves its own accuracy over time.

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

# 4. Install IBKR API (see § IBKR Setup below)

# 5. Run backtest
make backtest      # HTML report in reports/

# 6. Paper trade
make paper         # connect TWS on port 7497

# 7. Monitor
make dashboard     # http://localhost:5000
```

---

## IBKR Setup

The IBKR Python API is **not on PyPI** — you must install it manually.

1. Download TWS API from https://interactivebrokers.github.io/
2. Extract the zip
3. Navigate to `TWS_API/source/pythonclient/`
4. Run: `pip install .`
5. Open Trader Workstation (TWS)
6. Enable API: `Edit → Global Configuration → API → Settings`
   - ✅ Enable ActiveX and Socket Clients
   - ✅ Allow connections from localhost only
   - Socket port: **7497** (paper) or **7496** (live)
7. Set `IBKR_PORT=7497` in your `.env`

---

## Project structure

```
trading-bot/
├── config/
│   └── config.yaml          # all tunable parameters
├── data/
│   ├── raw/                 # downloaded OHLCV bars
│   ├── processed/           # feature-engineered data
│   └── trading_bot.db       # SQLite: trades, positions, equity curve
├── logs/                    # rotating daily log files
├── models/                  # versioned ML meta-labeler models
├── reports/                 # backtest HTML reports
├── scripts/
│   ├── run_backtest.py      # make backtest
│   ├── run_live.py          # make paper / make live
│   ├── run_dashboard.py     # make dashboard
│   └── run_retrain.py       # make retrain
├── src/
│   ├── config.py            # settings singleton
│   ├── logger.py            # loguru setup
│   ├── data/                # Phase 2: DataLoader, features
│   ├── strategy/            # Phase 3: SignalEngine, meta-labeler
│   ├── risk/                # Phase 5: RiskManager, Kelly, circuit breakers
│   ├── backtest/            # Phase 4: Backtester, purged CV, HTML report
│   ├── ml/                  # Phase 6: SelfLearner, HRP optimiser
│   ├── execution/           # Phase 7: LiveTrader, IBKR order management
│   └── dashboard/           # Phase 8: Flask app, Telegram alerts
├── tests/                   # pytest test suite
├── .env.template            # copy to .env and fill in values
├── .gitignore
├── Makefile
├── README.md
└── requirements.txt
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
| 1 | Scaffold (this file) | ✅ |
| 2 | Data pipeline (DataLoader, fractional diff, features) | ⬜ |
| 3 | Signal engine (RSI/BB/EMA + meta-labeling) | ⬜ |
| 4 | Walk-forward backtester (purged CV, HTML report) | ⬜ |
| 5 | Risk manager (Kelly, ATR stops, circuit breakers) | ⬜ |
| 6 | Self-improving ML layer (RF + HRP weekly retrain) | ⬜ |
| 7 | IBKR live execution (bracket orders, state machine) | ⬜ |
| 8 | Dashboard + Telegram alerts | ⬜ |

---

## Book references

| Book | Applied in |
|------|-----------|
| López de Prado — *Advances in Financial Machine Learning* | Fractional diff, meta-labeling, purged CV, CPCV |
| López de Prado — *Machine Learning for Asset Managers* | HRP, feature importance, clustering |
| Ernest Chan — *Algorithmic Trading* | Mean reversion signals, backtesting discipline |
| Ernest Chan — *Quantitative Trading* | Kelly criterion, strategy selection |
| Nassim Taleb — *The Black Swan* | Fat-tail position caps, circuit breakers |
| Larry Harris — *Trading and Exchanges* | Avoid open/close 15min, execution microstructure |
| Antti Ilmanen — *Expected Returns* | Risk premia factor exposure |
| Mark Douglas — *Trading in the Zone* | Sizing discipline, half-Kelly rationale |

---

## Safety

- **Paper mode by default.** Live orders require `TRADING_MODE=live` in `.env` AND `--live` flag AND terminal confirmation.
- **Daily drawdown circuit breaker.** Bot halts if daily P&L drops below `-3%` of capital.
- **Max 3 simultaneous positions.** Never more than 20% of capital in open positions.
- **Hard position cap.** No single ticker ever exceeds `$2,500` regardless of Kelly.
- **Microstructure guard.** No orders in first or last 15 minutes of each session.

---

## Disclaimer

This software is for educational purposes. Trading equities involves significant  
financial risk. Past backtested performance does not guarantee future results.
