"""src/dashboard/app.py — Flask monitoring dashboard (Phase 8)."""
from __future__ import annotations
from datetime import datetime
from flask import Flask, jsonify, render_template_string
from src.config import settings, cfg, TICKERS


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = settings.DASHBOARD_SECRET_KEY

    @app.route("/api/status")
    def api_status():
        from src.data.store import DataStore
        from src.execution.scheduler import market_status
        store = DataStore()
        try:
            open_trades  = store.open_trades()
            daily_pnl    = store.daily_pnl_today()
            eq_curve     = store.load_equity_curve(days=1)
            equity = float(eq_curve.iloc[-1]["equity"]) if not eq_curve.empty else settings.TOTAL_CAPITAL
            return jsonify({
                "market": market_status(), "daily_pnl": round(daily_pnl,2),
                "equity": round(equity,2), "open_positions": len(open_trades),
                "open_tickers": open_trades["ticker"].tolist() if not open_trades.empty else [],
                "mode": settings.TRADING_MODE, "capital": settings.TOTAL_CAPITAL,
                "timestamp": datetime.utcnow().isoformat(),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/trades")
    def api_trades():
        from src.data.store import DataStore
        store = DataStore()
        try:
            trades = store.load_trades(closed_only=False)
            return jsonify([] if trades.empty else trades.tail(100).to_dict("records"))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/equity")
    def api_equity():
        from src.data.store import DataStore
        store = DataStore()
        try:
            curve = store.load_equity_curve(
                days=cfg.get("dashboard",{}).get("equity_curve_days", 90))
            if curve.empty:
                return jsonify({"dates":[],"values":[]})
            return jsonify({
                "dates":  curve["snapshot_date"].tolist(),
                "values": [round(v,2) for v in curve["equity"].tolist()],
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/signals")
    def api_signals():
        try:
            from src.data.loader import DataLoader
            from src.strategy.engine import StrategyEngine
            loader  = DataLoader(tickers=TICKERS)
            engine  = StrategyEngine(tickers=TICKERS)
            signals = []
            for ticker in TICKERS:
                bar = loader.get_latest(ticker)
                if bar is None: continue
                sig = engine.signal_engine.evaluate(ticker, bar)
                signals.append(sig.to_dict())
            return jsonify(sorted(signals, key=lambda s: abs(s["score"]), reverse=True))
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/performance")
    def api_performance():
        from src.data.store import DataStore
        from src.backtest.metrics import summary
        store  = DataStore()
        trades = store.load_trades(closed_only=True)
        if trades.empty: return jsonify({})
        return jsonify(summary(trades.to_dict("records"), settings.TOTAL_CAPITAL))

    @app.route("/")
    def index():
        refresh_ms = cfg.get("dashboard",{}).get("refresh_seconds",30) * 1000
        return render_template_string(
            DASHBOARD_HTML, tickers=TICKERS,
            mode=settings.TRADING_MODE,
            capital=settings.TOTAL_CAPITAL,
            refresh_ms=refresh_ms,
        )

    return app


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Trading Bot Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist@2.27.0/plotly.min.js"></script>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       background:#0f1117; color:#e0e0e0; padding:20px; }
h1  { font-size:20px; font-weight:600; color:#fff; margin-bottom:4px; }
h2  { font-size:12px; font-weight:500; color:#888; margin:20px 0 10px;
      text-transform:uppercase; letter-spacing:.5px; }
.sub { font-size:12px; color:#555; margin-bottom:20px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr));
         gap:10px; margin-bottom:20px; }
.card { background:#1a1d27; border:1px solid #2a2d3e; border-radius:10px; padding:14px; }
.card .lbl { font-size:10px; color:#555; text-transform:uppercase;
             letter-spacing:.5px; margin-bottom:6px; }
.card .val { font-size:20px; font-weight:600; }
.gr  { color:#4ade80; } .rd { color:#f87171; }
.yl  { color:#facc15; } .gy { color:#888; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-bottom:20px; }
.panel { background:#1a1d27; border:1px solid #2a2d3e;
         border-radius:10px; padding:16px; }
table  { width:100%; border-collapse:collapse; font-size:12px; }
th { text-align:left; padding:6px 10px; color:#555; font-weight:500;
     border-bottom:1px solid #2a2d3e; }
td { padding:6px 10px; border-bottom:1px solid #161920; }
tr:hover td { background:#1f2535; }
.badge { display:inline-block; padding:2px 7px; border-radius:4px;
         font-size:10px; font-weight:500; }
.bg { background:#14532d; color:#4ade80; }
.br { background:#450a0a; color:#f87171; }
.by { background:#422006; color:#facc15; }
.dot { display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:5px; }
#foot { font-size:11px; color:#444; text-align:right; margin-top:12px; }
@media(max-width:640px){ .grid2{ grid-template-columns:1fr; } }
</style>
</head>
<body>
<h1>Trading Bot</h1>
<div class="sub">
  Mode: <strong>{{ mode.upper() }}</strong> &nbsp;|&nbsp;
  Capital: <strong>${{ "{:,.0f}".format(capital) }}</strong> &nbsp;|&nbsp;
  {{ tickers | join(', ') }}
</div>

<h2>Live status</h2>
<div class="cards">
  <div class="card"><div class="lbl">Market</div><div class="val gy" id="mkt">—</div></div>
  <div class="card"><div class="lbl">Daily P&L</div><div class="val" id="pnl">—</div></div>
  <div class="card"><div class="lbl">Equity</div><div class="val" id="eq">—</div></div>
  <div class="card"><div class="lbl">Positions</div><div class="val" id="pos">—</div></div>
  <div class="card"><div class="lbl">Win rate</div><div class="val" id="wr">—</div></div>
  <div class="card"><div class="lbl">Sharpe</div><div class="val" id="sh">—</div></div>
  <div class="card"><div class="lbl">CAGR</div><div class="val" id="cagr">—</div></div>
  <div class="card"><div class="lbl">Max DD</div><div class="val rd" id="mdd">—</div></div>
</div>

<div class="grid2">
  <div class="panel">
    <h2 style="margin-top:0">Equity curve</h2>
    <div id="eq-chart" style="height:220px;"></div>
  </div>
  <div class="panel">
    <h2 style="margin-top:0">Signal scan</h2>
    <table><thead><tr><th>Ticker</th><th>Score</th><th>RSI</th><th>Entry</th><th>Status</th></tr></thead>
    <tbody id="sig-body"><tr><td colspan="5" class="gy">Loading...</td></tr></tbody></table>
  </div>
</div>

<h2>Open positions</h2>
<div class="panel" style="margin-bottom:20px;">
  <table><thead><tr><th>Ticker</th><th>Side</th><th>Entry</th><th>Stop</th><th>Target</th><th>Opened</th></tr></thead>
  <tbody id="pos-body"><tr><td colspan="6" class="gy">No open positions</td></tr></tbody></table>
</div>

<h2>Recent trades</h2>
<div class="panel">
  <table><thead><tr><th>Date</th><th>Ticker</th><th>Side</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Reason</th></tr></thead>
  <tbody id="tr-body"><tr><td colspan="7" class="gy">Loading...</td></tr></tbody></table>
</div>

<div id="foot">Auto-refresh every {{ (refresh_ms//1000) }}s &nbsp;|&nbsp; Last update: <span id="ts">—</span></div>

<script>
const CAP = {{ capital }};

function clr(v){ return v>=0?'gr':'rd'; }
function fmt(v,d=2){ return '$'+parseFloat(v||0).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d}); }
function pct(v){ const n=parseFloat(v||0); return (n>=0?'+':'')+( n*100).toFixed(1)+'%'; }

async function loadStatus(){
  try{
    const d=await(await fetch('/api/status')).json();
    const m=d.market||{};
    document.getElementById('mkt').innerHTML=m.is_open
      ?'<span class="gr"><span class="dot" style="background:#4ade80"></span>Open</span>'
      :'<span class="gy"><span class="dot" style="background:#555"></span>Closed</span>';
    const p=d.daily_pnl||0;
    const el=document.getElementById('pnl');
    el.textContent=(p>=0?'+':'')+fmt(Math.abs(p));
    el.className='val '+clr(p);
    const eq=document.getElementById('eq');
    eq.textContent=fmt(d.equity||CAP,0);
    eq.className='val '+clr((d.equity||CAP)-CAP);
    document.getElementById('pos').textContent=(d.open_positions||0)+' / 3';
  }catch(e){console.error(e);}
}

async function loadPerf(){
  try{
    const d=await(await fetch('/api/performance')).json();
    if(!d.n_trades)return;
    const wr=d.win_rate||0;
    const wrEl=document.getElementById('wr');
    wrEl.textContent=(wr*100).toFixed(1)+'%';
    wrEl.className='val '+(wr>=0.45?'gr':wr>=0.38?'yl':'rd');
    const sh=d.sharpe||0;
    const shEl=document.getElementById('sh');
    shEl.textContent=sh.toFixed(2);
    shEl.className='val '+(sh>=1?'gr':sh>=0.5?'yl':'rd');
    const cagr=document.getElementById('cagr');
    cagr.textContent=pct(d.cagr);
    cagr.className='val '+clr(d.cagr);
    document.getElementById('mdd').textContent=(parseFloat(d.max_drawdown||0)*100).toFixed(1)+'%';
  }catch(e){console.error(e);}
}

async function loadEquity(){
  try{
    const d=await(await fetch('/api/equity')).json();
    if(!d.dates||!d.dates.length)return;
    const up=d.values[d.values.length-1]>=(d.values[0]||CAP);
    const c=up?'#4ade80':'#f87171';
    Plotly.react('eq-chart',[{
      x:d.dates,y:d.values,type:'scatter',mode:'lines',
      line:{color:c,width:2},fill:'tozeroy',
      fillcolor:c.replace(')',',0.08)').replace('#','rgba(').replace(/([0-9a-f]{2})/gi,(_,h)=>parseInt(h,16)+','),
    }],{
      paper_bgcolor:'transparent',plot_bgcolor:'transparent',
      font:{color:'#888',size:11},
      xaxis:{gridcolor:'#2a2d3e'},yaxis:{gridcolor:'#2a2d3e',tickprefix:'$'},
      margin:{t:5,r:5,b:30,l:55},
    },{responsive:true,displayModeBar:false});
  }catch(e){console.error(e);}
}

async function loadSignals(){
  try{
    const sigs=await(await fetch('/api/signals')).json();
    const tb=document.getElementById('sig-body');
    if(!Array.isArray(sigs)||!sigs.length){
      tb.innerHTML='<tr><td colspan="5" class="gy">No signals</td></tr>'; return;
    }
    tb.innerHTML=sigs.map(s=>{
      const st=s.is_actionable
        ?'<span class="badge bg">✅ GO</span>'
        :`<span class="gy" style="font-size:10px">${(s.blocked_reason||'no signal').slice(0,22)}</span>`;
      return `<tr>
        <td><strong>${s.ticker}</strong></td>
        <td>${parseFloat(s.score).toFixed(2)}</td>
        <td class="${s.rsi<10?'gr':s.rsi>90?'rd':''}">${parseFloat(s.rsi).toFixed(1)}</td>
        <td>${s.entry_price?fmt(s.entry_price):'—'}</td>
        <td>${st}</td>
      </tr>`;
    }).join('');
  }catch(e){console.error(e);}
}

async function loadTrades(){
  try{
    const all=await(await fetch('/api/trades')).json();
    if(!Array.isArray(all))return;
    const open=all.filter(t=>!t.exit_time);
    const pb=document.getElementById('pos-body');
    pb.innerHTML=open.length
      ?open.map(t=>`<tr>
          <td><strong>${t.ticker}</strong></td><td>${t.side}</td>
          <td>${fmt(t.entry_price)}</td>
          <td class="rd">${fmt(t.stop_price||0)}</td>
          <td class="gr">${fmt(t.target_price||0)}</td>
          <td>${(t.entry_time||'').slice(0,16)}</td>
        </tr>`).join('')
      :'<tr><td colspan="6" class="gy">No open positions</td></tr>';

    const closed=all.filter(t=>t.exit_time&&t.pnl!=null).reverse().slice(0,30);
    const tb=document.getElementById('tr-body');
    tb.innerHTML=closed.length
      ?closed.map(t=>{
          const p=parseFloat(t.pnl)||0;
          return `<tr>
            <td>${(t.entry_time||'').slice(0,10)}</td>
            <td><strong>${t.ticker}</strong></td>
            <td>${t.side}</td>
            <td>${fmt(t.entry_price)}</td>
            <td>${fmt(t.exit_price||0)}</td>
            <td class="${clr(p)}">${(p>=0?'+':'')+fmt(Math.abs(p))}</td>
            <td>${t.exit_reason||'—'}</td>
          </tr>`;
        }).join('')
      :'<tr><td colspan="7" class="gy">No closed trades yet</td></tr>';
  }catch(e){console.error(e);}
}

async function refresh(){
  await Promise.all([loadStatus(),loadPerf(),loadEquity(),loadSignals(),loadTrades()]);
  document.getElementById('ts').textContent=new Date().toLocaleTimeString();
}

refresh();
setInterval(refresh, {{ refresh_ms }});
</script>
</body>
</html>"""
