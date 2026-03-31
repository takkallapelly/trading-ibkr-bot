"""
src/logger.py
─────────────
Configures loguru for the entire project.
Import `get_logger` in any module — never configure loguru directly.

Usage:
    from src.logger import get_logger
    log = get_logger(__name__)
    log.info("Trade opened | ticker=TSLA | qty=10 | price=250.00")
"""

import sys
from pathlib import Path
from loguru import logger
from src.config import settings


def setup_logging() -> None:
    """
    Call once at process startup (run_live.py, run_backtest.py, etc.).
    Adds console + rotating file sink.
    """
    logger.remove()  # remove default stderr handler

    fmt = settings.LOG_LEVEL
    log_fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    # Console
    logger.add(
        sys.stderr,
        format=log_fmt,
        level=settings.LOG_LEVEL,
        colorize=True,
    )

    # Rotating file
    log_dir: Path = settings.LOGS_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_dir / "{time:YYYY-MM-DD}.log",
        format=log_fmt,
        level=settings.LOG_LEVEL,
        rotation=settings.LOG_ROTATION,
        retention="30 days",
        compression="zip",
        enqueue=True,   # thread-safe for live bot
    )


def get_logger(name: str):
    """Return a bound logger tagged with the calling module name."""
    return logger.bind(name=name)
