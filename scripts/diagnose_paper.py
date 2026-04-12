#!/usr/bin/env python3
"""
scripts/diagnose_paper.py
──────────────────────────
Reads the SQLite paper trade log and prints a structured diagnostic report
showing exactly where edge is leaking.

Usage:
    python scripts/diagnose_paper.py
    python scripts/diagnose_paper.py --html
    python scripts/diagnose_paper.py --html --out reports/my_diag.html
    python scripts/diagnose_paper.py --min-trades 10

Works on Windows, Mac, and Linux — no make required.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import click
from pathlib import Path
from rich.console import Console

console = Console()


@click.command()
@click.option("--html",       is_flag=True,  default=False, help="Also save HTML report")
@click.option("--out",        default=None,  help="HTML output path (default: reports/diagnostic_YYYY-MM-DD.html)")
@click.option("--min-trades", default=5,     type=int, help="Minimum closed trades required to run analysis")
def main(html: bool, out: str | None, min_trades: int) -> None:
    from src.logger import setup_logging
    setup_logging()

    from src.data.store import DataStore
    from src.diagnostics.trade_analyzer import TradeAnalyzer
    from src.diagnostics.report import DiagnosticReport
    from src.config import settings
    from datetime import date

    console.rule("[bold cyan]Paper Trade Diagnostic[/bold cyan]")

    store  = DataStore()
    trades = store.load_trades_full()

    if trades.empty or len(trades) < min_trades:
        console.print(
            f"[yellow]Only {len(trades)} closed trade(s) found "
            f"(minimum {min_trades} required for meaningful analysis).[/yellow]"
        )
        console.print("[dim]Keep paper trading — the diagnostic becomes useful after ~30 trades.[/dim]")
        raise SystemExit(0)

    console.print(f"[green]Loaded {len(trades)} closed trades from database.[/green]\n")

    az  = TradeAnalyzer(trades)
    rpt = DiagnosticReport(az)

    rpt.print_terminal()

    if html:
        out_path = Path(out) if out else (
            settings.REPORTS_DIR / f"diagnostic_{date.today().isoformat()}.html"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        rpt.save_html(out_path)
        console.print(f"\n[green]HTML report saved → {out_path}[/green]")
        console.print(f"[dim]Open in browser: file:///{out_path.resolve()}[/dim]")


if __name__ == "__main__":
    main()
