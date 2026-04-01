#!/usr/bin/env python3
"""
scripts/journal_report.py
──────────────────────────
Print a Douglas-style casino exercise report from your trade journal.

Usage:
    python scripts/journal_report.py
    python scripts/journal_report.py --sample 2
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import click
from src.logger import setup_logging, get_logger
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

setup_logging()
log = get_logger(__name__)
console = Console()


@click.command()
@click.option("--sample", default=None, type=int, help="Show specific sample number")
def main(sample):
    from src.dashboard.journal import TradeJournal, SAMPLE_SIZE
    from src.data.store import DataStore

    journal = TradeJournal()
    store   = DataStore()

    # Auto-sync closed trades from DB to journal
    trades_df = store.load_trades(closed_only=True)
    if not trades_df.empty:
        db_count   = len(trades_df)
        jrnl_count = len(journal._entries)
        if db_count > jrnl_count:
            new_trades = trades_df.tail(db_count - jrnl_count)
            for _, row in new_trades.iterrows():
                journal.record_trade(row.to_dict())
            log.info(f"Synced {db_count - jrnl_count} new trades from database")

    if not journal._entries:
        console.print(Panel(
            "[yellow]No trades yet in journal.[/yellow]\n\n"
            "The casino exercise starts when your first paper trade fires.\n"
            "Run [cyan]python scripts/run_live.py --paper[/cyan] during market hours.",
            title="Mark Douglas — Casino Exercise",
            border_style="blue"
        ))
        return

    # ── Casino Exercise Status ────────────────────────────────────────────────
    status = journal.casino_exercise_status()
    total  = journal.total_stats()

    console.print(Panel(
        f"[bold]Trades completed:[/bold] {status['trades_completed']}\n"
        f"[bold]Current sample:[/bold]   {status['current_sample']}\n"
        f"[bold]In this sample:[/bold]   {status['trades_in_sample']} / {SAMPLE_SIZE}\n"
        f"[bold]Remaining:[/bold]        {status['trades_remaining']}\n"
        f"[bold]Current win rate:[/bold] {status['current_win_rate']:.1%}\n"
        f"[bold]Current P&L:[/bold]      ${status['current_pnl']:,.2f}\n"
        f"[bold]Discipline:[/bold]       {'✅ No overrides' if status['discipline_intact'] else '⚠️  Overrides detected!'}\n\n"
        f"[italic]{status['douglas_message']}[/italic]",
        title="🎰 Casino Exercise Status",
        border_style="green" if status["discipline_intact"] else "red"
    ))

    # ── Sample History ────────────────────────────────────────────────────────
    all_samples = journal.all_sample_stats()

    if sample:
        all_samples = [s for s in all_samples if s["sample_number"] == sample]

    sample_table = Table(title="Sample History (Douglas 20-Trade Blocks)", show_lines=True)
    sample_table.add_column("Sample", style="cyan", justify="center")
    sample_table.add_column("Trades", justify="right")
    sample_table.add_column("Win %",  justify="right")
    sample_table.add_column("P&L",    justify="right")
    sample_table.add_column("Overrides", justify="center")
    sample_table.add_column("Verdict", style="white")

    for s in all_samples:
        wr_color = "green" if s["win_rate"] >= 0.45 else "yellow" if s["win_rate"] >= 0.40 else "red"
        pnl_color = "green" if s["total_pnl"] >= 0 else "red"
        over_color = "green" if s["overrides"] == 0 else "red"

        sample_table.add_row(
            str(s["sample_number"]),
            f"{s['closed_trades']} / {SAMPLE_SIZE}",
            f"[{wr_color}]{s['win_rate']:.1%}[/{wr_color}]",
            f"[{pnl_color}]${s['total_pnl']:,.2f}[/{pnl_color}]",
            f"[{over_color}]{s['overrides']}[/{over_color}]",
            s["douglas_verdict"][:60],
        )

    console.print(sample_table)

    # ── Overall Stats ─────────────────────────────────────────────────────────
    console.print(Panel(
        f"[bold]Total trades:[/bold]      {total.get('total_trades', 0)}\n"
        f"[bold]Overall win rate:[/bold]  {total.get('win_rate', 0):.1%}\n"
        f"[bold]Total P&L:[/bold]         ${total.get('total_pnl', 0):,.2f}\n"
        f"[bold]Total overrides:[/bold]   {total.get('total_overrides', 0)}\n"
        f"[bold]Discipline score:[/bold]  {total.get('discipline_score', 1.0):.1%}\n"
        f"[bold]Samples completed:[/bold] {total.get('samples_completed', 0)}\n\n"
        "[italic]'The goal of any trader is to turn profits on a regular basis.\n"
        " The determining factor is psychological.' — Mark Douglas[/italic]",
        title="Overall Performance",
        border_style="blue"
    ))


if __name__ == "__main__":
    main()
