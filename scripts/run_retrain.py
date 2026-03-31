#!/usr/bin/env python3
"""
scripts/run_retrain.py
──────────────────────
Manually trigger the weekly ML meta-labeler retraining.
Normally invoked automatically by the scheduler inside run_live.py every Sunday.

Usage:
    python scripts/run_retrain.py
    python scripts/run_retrain.py --lookback 90   # use 90 days of trade history
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import click
from src.logger import setup_logging, get_logger
from src.config import settings

setup_logging()
log = get_logger(__name__)


@click.command()
@click.option("--lookback", default=settings.ML_LOOKBACK_DAYS, type=int,
              help="Days of trade history to train on")
def main(lookback: int) -> None:
    from src.ml.learner import SelfLearner

    log.info(f"Starting ML retrain | lookback={lookback} days")
    learner = SelfLearner()
    result = learner.retrain(lookback_days=lookback)
    log.success(
        f"Retrain complete | accuracy={result['accuracy']:.1%} | "
        f"n_trades={result['n_trades']} | model saved → {result['model_path']}"
    )


if __name__ == "__main__":
    main()
