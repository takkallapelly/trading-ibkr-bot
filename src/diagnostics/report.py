"""
src/diagnostics/report.py
──────────────────────────
Renders TradeAnalyzer results as:
  - Rich terminal tables (for scripts/diagnose_paper.py)
  - Self-contained HTML file (saved to reports/)
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich import box

from src.diagnostics.trade_analyzer import TradeAnalyzer

console = Console()

_SEVERITY_COLOR = {"HIGH": "red", "MEDIUM": "yellow", "LOW": "green"}


class DiagnosticReport:
    def __init__(self, analyzer: TradeAnalyzer):
        self._az = analyzer

    # ── Terminal output ───────────────────────────────────────────────────────

    def print_terminal(self) -> None:
        """Print full diagnostic to terminal using Rich tables."""
        stats = self._az.overall_stats()

        t = Table(title="Overall Performance", box=box.SIMPLE_HEAVY)
        t.add_column("Metric", style="cyan")
        t.add_column("Value",  style="yellow", justify="right")
        for k, v in stats.items():
            if isinstance(v, float):
                if "rate" in k:
                    val = f"{v:.1%}"
                elif "pnl" in k or "win" in k or "loss" in k or "trade" in k:
                    val = f"${v:,.2f}"
                else:
                    val = f"{v:.3f}"
            else:
                val = str(v)
            t.add_row(k.replace("_", " ").title(), val)
        console.print(t)

        by_ticker = self._az.by_ticker()
        if not by_ticker.empty:
            t2 = Table(title="Performance by Ticker", box=box.SIMPLE)
            t2.add_column("Ticker",    style="cyan")
            t2.add_column("Trades",   justify="right")
            t2.add_column("Win %",    justify="right", style="green")
            t2.add_column("Total P&L",justify="right")
            t2.add_column("Avg P&L",  justify="right")
            for idx, row in by_ticker.iterrows():
                pnl_color = "green" if row["total_pnl"] >= 0 else "red"
                t2.add_row(
                    str(idx),
                    str(int(row["n_trades"])),
                    f"{row['win_rate']:.1%}",
                    f"[{pnl_color}]${row['total_pnl']:,.0f}[/{pnl_color}]",
                    f"${row['avg_pnl']:,.0f}",
                )
            console.print(t2)

        by_hour = self._az.by_hour()
        if not by_hour.empty:
            t3 = Table(title="Performance by Hour of Day (ET)", box=box.SIMPLE)
            t3.add_column("Hour",     style="cyan")
            t3.add_column("Trades",   justify="right")
            t3.add_column("Win %",    justify="right")
            t3.add_column("Total P&L",justify="right")
            for idx, row in by_hour.iterrows():
                pnl_color = "green" if row["total_pnl"] >= 0 else "red"
                t3.add_row(
                    f"{idx}:00",
                    str(int(row["n_trades"])),
                    f"{row['win_rate']:.1%}",
                    f"[{pnl_color}]${row['total_pnl']:,.0f}[/{pnl_color}]",
                )
            console.print(t3)

        by_score = self._az.by_score_bucket()
        if not by_score.empty:
            t4 = Table(title="Performance by Signal Score Bucket", box=box.SIMPLE)
            t4.add_column("Score Range", style="cyan")
            t4.add_column("Trades",      justify="right")
            t4.add_column("Win %",       justify="right")
            t4.add_column("Total P&L",   justify="right")
            for idx, row in by_score.iterrows():
                pnl_color = "green" if row["total_pnl"] >= 0 else "red"
                t4.add_row(
                    str(idx),
                    str(int(row["n_trades"])),
                    f"{row['win_rate']:.1%}",
                    f"[{pnl_color}]${row['total_pnl']:,.0f}[/{pnl_color}]",
                )
            console.print(t4)

        leakage = self._az.edge_leakage_report()
        t5 = Table(title="Edge Leakage Findings", box=box.SIMPLE_HEAVY)
        t5.add_column("Severity", style="bold", width=8)
        t5.add_column("Finding")
        for finding, severity in leakage:
            color = _SEVERITY_COLOR.get(severity, "white")
            t5.add_row(f"[{color}]{severity}[/{color}]", finding)
        console.print(t5)

    # ── HTML output ───────────────────────────────────────────────────────────

    def save_html(self, output_path: Path) -> None:
        """Save a self-contained HTML diagnostic report."""
        stats   = self._az.overall_stats()
        by_t    = self._az.by_ticker()
        by_h    = self._az.by_hour()
        by_s    = self._az.by_score_bucket()
        by_d    = self._az.by_day_of_week()
        by_exit = self._az.by_exit_reason()
        leakage = self._az.edge_leakage_report()

        def df_to_html(df, title: str) -> str:
            if df is None or df.empty:
                return f"<h3>{title}</h3><p>No data</p>"
            styled = df.to_html(classes="table", border=0, float_format=lambda x: f"{x:.2f}")
            return f"<h3>{title}</h3>{styled}"

        leakage_html = "".join(
            f'<div class="finding {sev.lower()}"><strong>{sev}:</strong> {txt}</div>'
            for txt, sev in leakage
        )

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Trade Diagnostic Report</title>
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; background:#0d1117; color:#e6edf3; margin:0; padding:20px; }}
  h1 {{ color:#58a6ff; }} h3 {{ color:#79c0ff; border-bottom:1px solid #30363d; padding-bottom:6px; }}
  .table {{ border-collapse:collapse; width:100%; margin-bottom:24px; }}
  .table th {{ background:#161b22; color:#8b949e; padding:8px 12px; text-align:left; font-size:12px; }}
  .table td {{ padding:8px 12px; border-bottom:1px solid #21262d; font-size:13px; }}
  .finding {{ padding:10px 14px; margin:6px 0; border-radius:6px; }}
  .finding.high   {{ background:#3d1a1a; border-left:4px solid #f85149; }}
  .finding.medium {{ background:#2d2208; border-left:4px solid #d29922; }}
  .finding.low    {{ background:#0d2d1a; border-left:4px solid #3fb950; }}
  .stats-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin-bottom:24px; }}
  .stat-card {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:14px; }}
  .stat-card .label {{ font-size:11px; color:#8b949e; text-transform:uppercase; }}
  .stat-card .value {{ font-size:22px; font-weight:600; color:#58a6ff; margin-top:4px; }}
  .generated {{ color:#8b949e; font-size:11px; margin-top:32px; }}
</style>
</head>
<body>
<h1>Trade Diagnostic Report</h1>
<p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

<div class="stats-grid">
  <div class="stat-card"><div class="label">Total Trades</div><div class="value">{stats['n_trades']}</div></div>
  <div class="stat-card"><div class="label">Win Rate</div><div class="value">{stats['win_rate']:.1%}</div></div>
  <div class="stat-card"><div class="label">Total P&amp;L</div><div class="value">${stats['total_pnl']:,.0f}</div></div>
  <div class="stat-card"><div class="label">Expectancy</div><div class="value">${stats['expectancy']:,.0f}</div></div>
  <div class="stat-card"><div class="label">Profit Factor</div><div class="value">{stats['profit_factor']:.2f}</div></div>
  <div class="stat-card"><div class="label">Stop-Out Rate</div><div class="value">{stats['stop_out_rate']:.1%}</div></div>
</div>

<h3>Edge Leakage Findings</h3>
{leakage_html}

{df_to_html(by_t, "Performance by Ticker")}
{df_to_html(by_h, "Performance by Hour of Day (ET)")}
{df_to_html(by_d, "Performance by Day of Week")}
{df_to_html(by_s, "Performance by Signal Score Bucket")}
{df_to_html(by_exit, "Performance by Exit Reason")}

<p class="generated">Generated by trading-ibkr-bot diagnostics engine</p>
</body>
</html>"""
        Path(output_path).write_text(html, encoding="utf-8")
