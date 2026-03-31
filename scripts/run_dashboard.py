#!/usr/bin/env python3
"""
scripts/run_dashboard.py
────────────────────────
Launch the monitoring dashboard (Flask + Plotly).

Usage:
    python scripts/run_dashboard.py
    python scripts/run_dashboard.py --port 8080
    python scripts/run_dashboard.py --host 0.0.0.0
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import click
from src.logger import setup_logging, get_logger
from src.config import settings

setup_logging()
log = get_logger(__name__)


@click.command()
@click.option("--host", default=settings.DASHBOARD_HOST, help="Bind host")
@click.option("--port", default=settings.DASHBOARD_PORT, help="Bind port", type=int)
@click.option("--debug", is_flag=True, default=False, help="Flask debug mode")
def main(host: str, port: int, debug: bool) -> None:
    from src.dashboard.app import create_app

    log.info(f"Dashboard starting | http://{host}:{port}")
    app = create_app()
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
