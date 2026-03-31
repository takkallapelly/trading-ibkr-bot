"""
src/config.py
─────────────
Single source of truth for all settings.
Every module imports from here — never reads files directly.

Usage:
    from src.config import settings, cfg
    print(settings.TOTAL_CAPITAL)     # .env value
    print(cfg["signals"]["rsi"]["period"])  # config.yaml value
"""

import os
from pathlib import Path
from dotenv import load_dotenv
import yaml
from loguru import logger

# ── Locate project root (one level above this file) ───────────────────────────
ROOT = Path(__file__).parent.parent
ENV_FILE = ROOT / ".env"
CONFIG_FILE = ROOT / "config" / "config.yaml"

# ── Load .env ─────────────────────────────────────────────────────────────────
load_dotenv(ENV_FILE)


# ── Typed settings from environment ───────────────────────────────────────────
class Settings:
    """
    Typed wrapper around environment variables.
    Provides defaults so the bot never crashes on a missing key.
    """

    # IBKR
    IBKR_HOST: str = os.getenv("IBKR_HOST", "127.0.0.1")
    IBKR_PORT: int = int(os.getenv("IBKR_PORT", 7497))
    IBKR_CLIENT_ID: int = int(os.getenv("IBKR_CLIENT_ID", 1))

    # Mode guard: prevents live orders unless explicitly set
    TRADING_MODE: str = os.getenv("TRADING_MODE", "paper")

    @property
    def is_live(self) -> bool:
        return self.TRADING_MODE.lower() == "live"

    # Capital
    TOTAL_CAPITAL: float = float(os.getenv("TOTAL_CAPITAL", 25000))
    MAX_POSITION_PCT: float = float(os.getenv("MAX_POSITION_PCT", 0.10))
    MAX_PORTFOLIO_HEAT: float = float(os.getenv("MAX_PORTFOLIO_HEAT", 0.20))
    DAILY_DRAWDOWN_LIMIT: float = float(os.getenv("DAILY_DRAWDOWN_LIMIT", 0.03))

    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{ROOT}/data/trading_bot.db")

    # Dashboard
    DASHBOARD_HOST: str = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    DASHBOARD_PORT: int = int(os.getenv("DASHBOARD_PORT", 5000))
    DASHBOARD_SECRET_KEY: str = os.getenv("DASHBOARD_SECRET_KEY", "dev-secret-key")

    # ML
    ML_RETRAIN_DAY: str = os.getenv("ML_RETRAIN_DAY", "sunday")
    ML_LOOKBACK_DAYS: int = int(os.getenv("ML_LOOKBACK_DAYS", 60))
    ML_MIN_TRADES: int = int(os.getenv("ML_MIN_TRADES", 30))

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_ROTATION: str = os.getenv("LOG_ROTATION", "7 days")

    # Paths (always absolute)
    ROOT: Path = ROOT
    DATA_DIR: Path = ROOT / "data"
    MODELS_DIR: Path = ROOT / "models"
    REPORTS_DIR: Path = ROOT / "reports"
    LOGS_DIR: Path = ROOT / "logs"

    def validate(self) -> None:
        """Call on startup to catch misconfiguration early."""
        errors = []
        if self.is_live and not self.TELEGRAM_BOT_TOKEN:
            errors.append("TELEGRAM_BOT_TOKEN required in live mode")
        if self.TOTAL_CAPITAL <= 0:
            errors.append("TOTAL_CAPITAL must be positive")
        if not (0 < self.MAX_POSITION_PCT <= 1):
            errors.append("MAX_POSITION_PCT must be between 0 and 1")
        if errors:
            raise EnvironmentError(
                "Configuration errors:\n" + "\n".join(f"  • {e}" for e in errors)
            )
        logger.info(
            f"Config OK | mode={self.TRADING_MODE} | capital=${self.TOTAL_CAPITAL:,.0f}"
        )


# ── Load YAML config ──────────────────────────────────────────────────────────
def _load_yaml() -> dict:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"config.yaml not found at {CONFIG_FILE}")
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── Module-level singletons (import these everywhere) ─────────────────────────
settings = Settings()
cfg: dict = _load_yaml()

# Convenience shortcuts
TICKERS: list[str] = cfg["tickers"]["universe"]
