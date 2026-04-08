#!/usr/bin/env python3
"""
scripts/diagnose.py
───────────────────
Shows live RSI, BB, score values for all tickers right now.
Run this anytime to see exactly what the bot is seeing.

Usage:
    python scripts/diagnose.py
    python scripts/diagnose.py --watch      # refresh every 30 seconds
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import click
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.live import Live
from src.logger import setup_logging
from src.config import TICKERS

setup_logging()  # suppress noisy logs during diagnosis
console = Console()


def fetch_and_show():
    from src.data.intraday import IntradayFetcher, _intraday_cfg
    from src.strategy.signals import SignalEngine

    intraday_cfg = _intraday_cfg()
    fetcher = IntradayFetcher(tickers=TICKERS, use_ibkr=False)

    console.print(f"\n[cyan]Fetching 5-min bars for {TICKERS}...[/cyan]")
    fetcher.warmup(days=3)

    engine = SignalEngine(config=intraday_cfg)
    sig_cfg = intraday_cfg.get("signals", {})
    rsi_oversold   = sig_cfg.get("rsi", {}).get("oversold",  15)
    rsi_overbought = sig_cfg.get("rsi", {}).get("overbought", 85)
    min_score      = sig_cfg.get("min_signal_score", 0.65)

    table = Table(
        title=f"[bold]Live Signal Diagnostics — {datetime.now().strftime('%Y-%m-%d %H:%M:%S CET')}[/bold]",
        show_lines=True,
    )
    table.add_column("Ticker",    style="cyan",    no_wrap=True, width=8)
    table.add_column("Close $",   style="white",   justify="right", width=10)
    table.add_column("RSI(2)",    justify="right", width=8)
    table.add_column("BB%",       justify="right", width=8)
    table.add_column("EMA Diff",  justify="right", width=10)
    table.add_column("Score",     justify="right", width=8)
    table.add_column("Vol Ratio", justify="right", width=10)
    table.add_column("ATR%",      justify="right", width=8)
    table.add_column("Signal",    width=28)
    table.add_column("Blocked By", width=30)

    for ticker in TICKERS:
        bar = fetcher.get_latest(ticker)
        if bar is None:
            table.add_row(ticker, "N/A", "—","—","—","—","—","—","[red]No data[/red]", "")
            continue

        close     = float(bar.get("close", 0))
        rsi       = float(bar.get("rsi_2", 50))
        bb_pct    = float(bar.get("bb_pct", 0.5))
        ema_diff  = float(bar.get("ema_diff", 0))
        score     = float(bar.get("signal_score", 0))
        vol_ratio = float(bar.get("vol_ratio", 1))
        atr_pct   = float(bar.get("atr_pct", 0))
        atr       = float(bar.get("atr", 0))

        # RSI colour
        if rsi < rsi_oversold:
            rsi_str = f"[bold green]{rsi:.1f}[/bold green]"
        elif rsi > rsi_overbought:
            rsi_str = f"[bold red]{rsi:.1f}[/bold red]"
        else:
            rsi_str = f"[yellow]{rsi:.1f}[/yellow]"

        # Score colour
        if abs(score) >= min_score:
            score_str = f"[bold green]{score:.2f}[/bold green]"
        elif abs(score) >= 0.5:
            score_str = f"[yellow]{score:.2f}[/yellow]"
        else:
            score_str = f"[dim]{score:.2f}[/dim]"

        # Evaluate signal
        signal = engine.evaluate(ticker, bar)

        if signal.is_actionable:
            sig_str = f"[bold green]✓ {signal.direction.value}  entry=${signal.entry_price:.2f}[/bold green]"
            blocked_str = ""
        elif abs(score) >= min_score:
            sig_str = f"[red]✗ BLOCKED[/red]"
            blocked_str = f"[red]{signal.blocked_reason}[/red]"
        else:
            # Show how far from threshold
            gap = min_score - abs(score)
            sig_str = f"[dim]No signal[/dim]"
            blocked_str = f"[dim]score gap: {gap:.2f} | RSI gap to <{rsi_oversold}: {rsi-rsi_oversold:.1f}[/dim]"

        table.add_row(
            ticker,
            f"${close:.2f}",
            rsi_str,
            f"{bb_pct:.2f}",
            f"{ema_diff*100:.2f}%",
            score_str,
            f"{vol_ratio:.2f}x",
            f"{atr_pct*100:.2f}%",
            sig_str,
            blocked_str,
        )

    console.print(table)

    # Summary
    console.print(f"\n[bold]Thresholds:[/bold] RSI oversold < [green]{rsi_oversold}[/green] | "
                  f"RSI overbought > [red]{rsi_overbought}[/red] | "
                  f"Min score ≥ [yellow]{min_score}[/yellow]\n")

    console.print(
        "[bold cyan]Market Context:[/bold cyan] If RSI values are between 20–70 and not hitting extremes,\n"
        "the market is trending (not mean-reverting). RSI(2) strategy needs choppy/range-bound markets.\n"
        "High VIX + strong trend = fewer signals. This is the strategy working correctly.\n"
    )


@click.command()
@click.option("--watch", is_flag=True, help="Refresh every 30 seconds")
def main(watch):
    if watch:
        while True:
            console.clear()
            fetch_and_show()
            console.print("[dim]Refreshing in 30 seconds... Ctrl+C to stop[/dim]")
            time.sleep(30)
    else:
        fetch_and_show()


if __name__ == "__main__":
    main()
