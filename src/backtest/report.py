"""
src/backtest/report.py
───────────────────────
Generates a self-contained HTML backtest report.
No external dependencies — pure HTML/CSS/JS with inline Plotly charts.
Open the output file in any browser, no server needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime

from src.backtest.metrics import equity_curve_from_trades


def generate_html_report(
    trades: list[dict],
    metrics: dict,
    tickers: list[str],
    start: str,
    end: str,
    capital: float,
    output: Path,
) -> None:
    """Generate and save an HTML backtest report."""

    closed = [t for t in trades if t.get("pnl") is not None]

    # ── Equity curve data ─────────────────────────────────────────────────────
    eq = equity_curve_from_trades(closed, capital)
    eq_dates  = [str(d)[:10] for d in eq.index]
    eq_values = [round(v, 2) for v in eq.values]

    # ── Trade distribution ────────────────────────────────────────────────────
    pnls = [t["pnl"] for t in closed]

    # ── Per-ticker stats ──────────────────────────────────────────────────────
    ticker_rows = ""
    ticker_data: dict[str, list] = {}
    for t in closed:
        ticker_data.setdefault(t["ticker"], []).append(t)

    for ticker in sorted(ticker_data):
        ts   = ticker_data[ticker]
        pnl  = sum(t["pnl"] for t in ts)
        wr   = sum(1 for t in ts if t["pnl"] > 0) / len(ts)
        best = max(t["pnl"] for t in ts)
        worst= min(t["pnl"] for t in ts)
        color = "profit" if pnl > 0 else "loss"
        ticker_rows += f"""
        <tr>
            <td>{ticker}</td>
            <td>{len(ts)}</td>
            <td>{wr:.1%}</td>
            <td class="{color}">${pnl:,.0f}</td>
            <td class="profit">${best:,.0f}</td>
            <td class="loss">${worst:,.0f}</td>
            <td>${pnl/len(ts):.2f}</td>
        </tr>"""

    # ── Recent trades table ───────────────────────────────────────────────────
    recent = sorted(closed, key=lambda t: t.get("exit_time",""), reverse=True)[:50]
    trade_rows = ""
    for t in recent:
        color = "profit" if t["pnl"] > 0 else "loss"
        trade_rows += f"""
        <tr>
            <td>{t.get('entry_time','')[:10]}</td>
            <td>{t['ticker']}</td>
            <td>{t['side']}</td>
            <td>${t['entry_price']:.2f}</td>
            <td>${t['exit_price']:.2f}</td>
            <td>{t.get('exit_reason','')}</td>
            <td class="{color}">${t['pnl']:,.2f}</td>
        </tr>"""

    m = metrics
    total_color  = "profit" if m["total_pnl"] >= 0 else "loss"
    sharpe_color = "profit" if m["sharpe"] >= 1.0 else ("neutral" if m["sharpe"] >= 0.5 else "loss")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Backtest Report — {start} to {end}</title>
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist@2.27.0/plotly.min.js"></script>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          background:#0f1117; color:#e0e0e0; padding:24px; }}
  h1   {{ font-size:24px; font-weight:600; margin-bottom:4px; color:#fff; }}
  h2   {{ font-size:16px; font-weight:500; margin:24px 0 12px; color:#ccc; }}
  .sub {{ font-size:13px; color:#888; margin-bottom:24px; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin-bottom:24px; }}
  .card {{ background:#1a1d27; border:1px solid #2a2d3e; border-radius:10px; padding:16px; }}
  .card .label {{ font-size:11px; color:#888; text-transform:uppercase; letter-spacing:.5px; margin-bottom:6px; }}
  .card .value {{ font-size:22px; font-weight:600; }}
  .profit {{ color:#4ade80; }}
  .loss   {{ color:#f87171; }}
  .neutral{{ color:#facc15; }}
  .chart  {{ background:#1a1d27; border:1px solid #2a2d3e; border-radius:10px; padding:16px; margin-bottom:24px; }}
  table   {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th      {{ text-align:left; padding:8px 12px; color:#888; font-weight:500;
             border-bottom:1px solid #2a2d3e; }}
  td      {{ padding:8px 12px; border-bottom:1px solid #1a1d27; }}
  tr:hover td {{ background:#1a1d27; }}
  .table-wrap {{ background:#141720; border:1px solid #2a2d3e; border-radius:10px;
                 overflow:hidden; margin-bottom:24px; }}
  .badge  {{ display:inline-block; padding:2px 8px; border-radius:4px; font-size:11px; }}
  .badge.good {{ background:#14532d; color:#4ade80; }}
  .badge.bad  {{ background:#450a0a; color:#f87171; }}
</style>
</head>
<body>

<h1>Backtest Report</h1>
<div class="sub">
  Strategy: RSI(2) + Bollinger Band + EMA cross &nbsp;|&nbsp;
  Period: {start} → {end} &nbsp;|&nbsp;
  Tickers: {', '.join(tickers)} &nbsp;|&nbsp;
  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}
</div>

<!-- ── KPI Cards ── -->
<div class="cards">
  <div class="card">
    <div class="label">Total P&L</div>
    <div class="value {total_color}">${m['total_pnl']:,.0f}</div>
  </div>
  <div class="card">
    <div class="label">Total Return</div>
    <div class="value {total_color}">{m['total_return']:.1%}</div>
  </div>
  <div class="card">
    <div class="label">CAGR</div>
    <div class="value {'profit' if m['cagr']>0 else 'loss'}">{m['cagr']:.1%}</div>
  </div>
  <div class="card">
    <div class="label">Sharpe Ratio</div>
    <div class="value {sharpe_color}">{m['sharpe']:.2f}</div>
  </div>
  <div class="card">
    <div class="label">Win Rate</div>
    <div class="value {'profit' if m['win_rate']>0.5 else 'loss'}">{m['win_rate']:.1%}</div>
  </div>
  <div class="card">
    <div class="label">Max Drawdown</div>
    <div class="value loss">{m['max_drawdown']:.1%}</div>
  </div>
  <div class="card">
    <div class="label">Profit Factor</div>
    <div class="value {'profit' if m['profit_factor']>1 else 'loss'}">{m['profit_factor']:.2f}</div>
  </div>
  <div class="card">
    <div class="label">Total Trades</div>
    <div class="value">{m['n_trades']}</div>
  </div>
  <div class="card">
    <div class="label">Expectancy</div>
    <div class="value {'profit' if m['expectancy']>0 else 'loss'}">${m['expectancy']:.2f}</div>
  </div>
  <div class="card">
    <div class="label">Sortino</div>
    <div class="value {'profit' if m['sortino']>1 else 'neutral'}">{m['sortino']:.2f}</div>
  </div>
  <div class="card">
    <div class="label">Calmar</div>
    <div class="value {'profit' if m['calmar']>1 else 'neutral'}">{m['calmar']:.2f}</div>
  </div>
  <div class="card">
    <div class="label">Final Equity</div>
    <div class="value">${m['final_equity']:,.0f}</div>
  </div>
</div>

<!-- ── Equity Curve ── -->
<h2>Equity curve</h2>
<div class="chart" id="equity-chart" style="height:320px;"></div>

<!-- ── P&L Distribution ── -->
<h2>Trade P&L distribution</h2>
<div class="chart" id="pnl-chart" style="height:260px;"></div>

<!-- ── Per-ticker table ── -->
<h2>Results by ticker</h2>
<div class="table-wrap">
<table>
  <thead><tr>
    <th>Ticker</th><th>Trades</th><th>Win %</th>
    <th>Total P&L</th><th>Best trade</th><th>Worst trade</th><th>Avg P&L</th>
  </tr></thead>
  <tbody>{ticker_rows}</tbody>
</table>
</div>

<!-- ── Recent trades ── -->
<h2>Recent trades (last 50)</h2>
<div class="table-wrap">
<table>
  <thead><tr>
    <th>Date</th><th>Ticker</th><th>Side</th>
    <th>Entry</th><th>Exit</th><th>Reason</th><th>P&L</th>
  </tr></thead>
  <tbody>{trade_rows}</tbody>
</table>
</div>

<script>
const eqDates  = {json.dumps(eq_dates)};
const eqValues = {json.dumps(eq_values)};
const pnls     = {json.dumps([round(p,2) for p in pnls])};

// Equity curve
Plotly.newPlot('equity-chart', [{{
  x: eqDates, y: eqValues, type: 'scatter', mode: 'lines',
  line: {{ color: '#4ade80', width: 2 }},
  fill: 'tozeroy', fillcolor: 'rgba(74,222,128,0.08)',
  name: 'Equity'
}}], {{
  paper_bgcolor:'transparent', plot_bgcolor:'transparent',
  font:{{ color:'#888', size:12 }},
  xaxis:{{ gridcolor:'#2a2d3e', showgrid:true }},
  yaxis:{{ gridcolor:'#2a2d3e', showgrid:true, tickprefix:'$' }},
  margin:{{ t:10, r:10, b:40, l:70 }}, showlegend:false
}}, {{responsive:true, displayModeBar:false}});

// P&L histogram
const winners = pnls.filter(p => p > 0);
const losers  = pnls.filter(p => p < 0);
Plotly.newPlot('pnl-chart', [
  {{ x: winners, type:'histogram', name:'Win', marker:{{ color:'#4ade80' }}, opacity:0.8 }},
  {{ x: losers,  type:'histogram', name:'Loss',marker:{{ color:'#f87171' }}, opacity:0.8 }},
], {{
  paper_bgcolor:'transparent', plot_bgcolor:'transparent',
  font:{{ color:'#888', size:12 }},
  xaxis:{{ gridcolor:'#2a2d3e', tickprefix:'$', title:'P&L per trade' }},
  yaxis:{{ gridcolor:'#2a2d3e', title:'Count' }},
  barmode:'overlay', margin:{{ t:10, r:10, b:50, l:60 }},
  legend:{{ bgcolor:'transparent' }}
}}, {{responsive:true, displayModeBar:false}});
</script>
</body>
</html>"""

    output.write_text(html, encoding="utf-8")
