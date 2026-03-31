# ══════════════════════════════════════════════════════════════════════════════
#  Makefile  —  all project commands
#  Usage: make <target>
# ══════════════════════════════════════════════════════════════════════════════

PYTHON     := python3
PIP        := $(PYTHON) -m pip
PYTEST     := $(PYTHON) -m pytest
SCRIPTS    := scripts

.DEFAULT_GOAL := help

# ── Colours for terminal output ───────────────────────────────────────────────
CYAN  := \033[0;36m
RESET := \033[0m

## ── Setup ────────────────────────────────────────────────────────────────────

.PHONY: help
help:                ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?##.*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-20s$(RESET) %s\n", $$1, $$2}'

.PHONY: install
install:             ## Install all Python dependencies
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@echo "✓ Dependencies installed"

.PHONY: env
env:                 ## Copy .env.template → .env (safe, won't overwrite)
	@if [ ! -f .env ]; then \
		cp .env.template .env; \
		echo "✓ .env created — fill in your values"; \
	else \
		echo ".env already exists — not overwritten"; \
	fi

.PHONY: dirs
dirs:                ## Create runtime directories (data, logs, models, reports)
	mkdir -p data/raw data/processed logs models reports
	touch data/raw/.gitkeep data/processed/.gitkeep \
		  logs/.gitkeep models/.gitkeep reports/.gitkeep
	@echo "✓ Runtime directories ready"

.PHONY: setup
setup: env dirs install  ## First-time setup: env + dirs + install
	@echo ""
	@echo "✓ Setup complete. Next steps:"
	@echo "  1. Edit .env with your IBKR credentials and Telegram token"
	@echo "  2. Install IBKR API: see README § IBKR Setup"
	@echo "  3. Run: make backtest"

## ── Core commands ────────────────────────────────────────────────────────────

.PHONY: backtest
backtest:            ## Run walk-forward backtest across full ticker universe
	$(PYTHON) $(SCRIPTS)/run_backtest.py

.PHONY: backtest-custom
backtest-custom:     ## Backtest with custom dates: make backtest-custom START=2022-01-01 END=2024-01-01
	$(PYTHON) $(SCRIPTS)/run_backtest.py --start $(START) --end $(END)

.PHONY: paper
paper:               ## Start paper trading bot (safe)
	$(PYTHON) $(SCRIPTS)/run_live.py --paper

.PHONY: live
live:                ## Start live trading bot (requires TRADING_MODE=live in .env + confirmation)
	$(PYTHON) $(SCRIPTS)/run_live.py --live

.PHONY: dashboard
dashboard:           ## Launch monitoring dashboard at http://localhost:5000
	$(PYTHON) $(SCRIPTS)/run_dashboard.py

.PHONY: retrain
retrain:             ## Manually trigger ML meta-labeler retraining
	$(PYTHON) $(SCRIPTS)/run_retrain.py

## ── Development ──────────────────────────────────────────────────────────────

.PHONY: test
test:                ## Run all tests
	$(PYTEST) tests/ -v --cov=src --cov-report=term-missing

.PHONY: test-fast
test-fast:           ## Run tests excluding slow integration tests
	$(PYTEST) tests/ -v -m "not slow"

.PHONY: lint
lint:                ## Run ruff linter
	$(PYTHON) -m ruff check src/ scripts/ tests/

.PHONY: format
format:              ## Auto-format with ruff
	$(PYTHON) -m ruff format src/ scripts/ tests/

## ── Data utilities ───────────────────────────────────────────────────────────

.PHONY: fetch-data
fetch-data:          ## Fetch fresh OHLCV data for all tickers
	$(PYTHON) -c "from src.data.loader import DataLoader; DataLoader().fetch_all()"

.PHONY: check-config
check-config:        ## Validate config.yaml and .env
	$(PYTHON) -c "from src.config import settings; settings.validate(); print('Config OK')"

## ── Git helpers ──────────────────────────────────────────────────────────────

.PHONY: init-repo
init-repo:           ## Initialise git repo and push skeleton to GitHub
	git init
	git add .
	git commit -m "chore: initial project scaffold (Phase 1)"
	@echo "Now run: git remote add origin <your-github-url> && git push -u origin main"

.PHONY: tag
tag:                 ## Tag current commit with version: make tag V=v1.2.0
	git tag -a $(V) -m "Release $(V)"
	git push origin $(V)

## ── Cleanup ──────────────────────────────────────────────────────────────────

.PHONY: clean
clean:               ## Remove Python cache files
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
	find . -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@echo "✓ Cache cleaned"

.PHONY: clean-all
clean-all: clean     ## Remove all generated files (reports, logs, models — NOT data)
	rm -rf reports/* logs/* models/*
	@echo "✓ Reports, logs, models cleared"
