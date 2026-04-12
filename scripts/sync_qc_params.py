#!/usr/bin/env python3
"""
scripts/sync_qc_params.py
──────────────────────────
Apply a validated QuantConnect parameter variant to config.yaml.

Usage:
    python scripts/sync_qc_params.py --list
    python scripts/sync_qc_params.py --variant wider_stop --dry-run
    python scripts/sync_qc_params.py --variant wider_stop

Works on Windows, Mac, and Linux — no make required.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import click
import yaml
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.table import Table

console = Console()
CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"

# Map QC variant keys → config.yaml nested key paths
_PARAM_MAP = {
    "RSI_OVERSOLD":    ("signals", "rsi", "oversold"),
    "RSI_OVERBOUGHT":  ("signals", "rsi", "overbought"),
    "BB_STD":          ("signals", "bollinger", "std_dev"),
    "MIN_SCORE":       ("signals", "min_signal_score"),
    "STOP_ATR_MULT":   ("risk", "stop_loss_atr_mult"),
    "TARGET_ATR_MULT": ("risk", "take_profit_atr_mult"),
}


def _set_nested(d: dict, keys: tuple, value) -> None:
    for key in keys[:-1]:
        d = d.setdefault(key, {})
    d[keys[-1]] = value


def _get_nested(d: dict, keys: tuple):
    for key in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(key, {})
    return d


@click.command()
@click.option("--variant",        default=None,  help="Variant name to apply")
@click.option("--list",           "list_variants", is_flag=True, default=False,
              help="List all available variants")
@click.option("--dry-run",        is_flag=True,  default=False,
              help="Show what would change without writing config.yaml")
def main(variant: str | None, list_variants: bool, dry_run: bool) -> None:
    from quantconnect.parameter_variants import VARIANTS

    if list_variants:
        t = Table(title="Available Parameter Variants")
        t.add_column("Name",        style="cyan",  no_wrap=True)
        t.add_column("Description", style="white")
        for name, params in VARIANTS.items():
            t.add_row(name, params["description"])
        console.print(t)
        return

    if not variant:
        console.print("[red]Error: provide --variant NAME or use --list to see options[/red]")
        raise SystemExit(1)

    if variant not in VARIANTS:
        console.print(f"[red]Unknown variant '{variant}'. Run --list to see options.[/red]")
        raise SystemExit(1)

    params = {k: v for k, v in VARIANTS[variant].items() if k != "description"}

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    console.print(f"\n[cyan]Variant:[/cyan] {variant}")
    console.print(f"[dim]{VARIANTS[variant]['description']}[/dim]\n")

    t = Table(title="Parameter Changes")
    t.add_column("Parameter",   style="cyan")
    t.add_column("Current",     style="yellow", justify="right")
    t.add_column("New Value",   style="green",  justify="right")
    t.add_column("Config Path", style="dim")

    for qc_key, new_val in params.items():
        path = _PARAM_MAP.get(qc_key)
        if not path:
            continue
        current  = _get_nested(cfg, path)
        path_str = " → ".join(path)
        t.add_row(qc_key, str(current), str(new_val), path_str)
        if not dry_run:
            _set_nested(cfg, path, new_val)

    console.print(t)

    if dry_run:
        console.print("\n[yellow]Dry run — config.yaml NOT modified.[/yellow]")
        console.print("[dim]Remove --dry-run to apply changes.[/dim]")
        return

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Audit log
    log_path = Path(__file__).parent.parent / "reports" / "qc_sync_log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp":     datetime.now().isoformat(),
        "variant":       variant,
        "description":   VARIANTS[variant]["description"],
        "params_applied": params,
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    console.print(f"\n[green]config.yaml updated with variant '{variant}'.[/green]")
    console.print(f"[dim]Sync logged to {log_path}[/dim]")
    console.print("\n[dim]Next steps:[/dim]")
    console.print("  python scripts/run_backtest.py        ← verify locally")
    console.print("  python scripts/run_live.py --paper    ← paper trade 2 weeks")


if __name__ == "__main__":
    main()
