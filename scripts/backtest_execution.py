"""
Execution Strategy Comparison Backtest
Tests 4 execution methods on the same RSI(2) signals:
1. Baseline   — buy at signal-day close (current backtest assumption)
2. Next Open  — buy at next day open (realistic current bot behavior)
3. Limit Dip  — buy at next day's low+10% of range (morning dip)
4. Adaptive   — IBKR adaptive simulation (0.15% better than open)
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import date
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()

UNIVERSE   = ["AVGO", "META", "MSFT", "COST"]
START_DATE = "2020-01-01"
END_DATE   = "2024-12-31"
CAPITAL    = 25000.0
POS_SIZE   = 2500.0   # per trade
COMMISSION = 0.005    # $0.005/share
SLIPPAGE   = 0.0005   # 0.05% market impact

RSI_BUY_THRESHOLD  = 10   # RSI(2) < 10  → buy signal
STOP_ATR_MULT      = 1.5
TARGET_ATR_MULT    = 3.0


def compute_rsi(series: pd.Series, period: int = 2) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs  = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl  = df["High"] - df["Low"]
    hc  = (df["High"] - df["Close"].shift()).abs()
    lc  = (df["Low"]  - df["Close"].shift()).abs()
    tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def get_data(ticker: str) -> pd.DataFrame:
    df = yf.download(ticker, start=START_DATE, end=END_DATE,
                     auto_adjust=True, progress=False)
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df["rsi"]  = compute_rsi(df["Close"])
    df["atr"]  = compute_atr(df)
    df["ema9"]  = df["Close"].ewm(span=9).mean()
    df["ema20"] = df["Close"].ewm(span=20).mean()
    df["vol_ratio"] = df["Volume"] / df["Volume"].rolling(20).mean()
    return df.dropna()


def simulate_execution(df: pd.DataFrame, method: str) -> list[dict]:
    """
    Simulate trades using different execution methods.
    Signal: RSI(2) < 10 AND EMA9 > EMA20 (trend filter)
    
    Methods:
      'close'    — entry at signal-day close (backtest ideal)
      'open'     — entry at NEXT day open (realistic market order)
      'limit'    — entry at NEXT day's low + 10% of day range (morning dip limit)
      'adaptive' — entry at 0.15% below NEXT day open (IBKR adaptive sim)
    """
    trades = []
    in_trade = False
    entry_price = stop = target = entry_idx = 0

    for i in range(20, len(df) - 1):
        row      = df.iloc[i]
        next_row = df.iloc[i + 1]

        # ── Exit check (if in trade) ───────────────────────────────────────
        if in_trade:
            # Check if stop or target hit during next day
            day_low  = float(next_row["Low"])
            day_high = float(next_row["High"])
            day_close= float(next_row["Close"])

            if day_low <= stop:
                exit_price = stop
                pnl = (exit_price - entry_price) * (POS_SIZE / entry_price)
                pnl -= COMMISSION * (POS_SIZE / entry_price) * 2
                trades.append({"pnl": pnl, "result": "STOP", "entry": entry_price, "exit": exit_price})
                in_trade = False

            elif day_high >= target:
                exit_price = target
                pnl = (exit_price - entry_price) * (POS_SIZE / entry_price)
                pnl -= COMMISSION * (POS_SIZE / entry_price) * 2
                trades.append({"pnl": pnl, "result": "TARGET", "entry": entry_price, "exit": exit_price})
                in_trade = False

            elif i - entry_idx >= 10:  # Max hold 10 days
                exit_price = day_close
                pnl = (exit_price - entry_price) * (POS_SIZE / entry_price)
                pnl -= COMMISSION * (POS_SIZE / entry_price) * 2
                trades.append({"pnl": pnl, "result": "TIMEOUT", "entry": entry_price, "exit": exit_price})
                in_trade = False

            continue

        # ── Signal check ───────────────────────────────────────────────────
        rsi       = float(row["rsi"])
        atr       = float(row["atr"])
        close     = float(row["Close"])
        ema9      = float(row["ema9"])
        ema20     = float(row["ema20"])
        vol_ratio = float(row["vol_ratio"])

        if not (rsi < RSI_BUY_THRESHOLD and ema9 > ema20 * 0.998 and vol_ratio > 0.5):
            continue
        if atr <= 0 or close <= 0:
            continue

        # ── Entry price by method ──────────────────────────────────────────
        next_open  = float(next_row["Open"])
        next_low   = float(next_row["Low"])
        next_high  = float(next_row["High"])
        day_range  = next_high - next_low

        if method == "close":
            # Current backtest: buy at signal day close
            entry_price = close * (1 + SLIPPAGE)

        elif method == "open":
            # Realistic: buy at next day market open
            entry_price = next_open * (1 + SLIPPAGE)

        elif method == "limit":
            # Limit order at morning dip (low + 10% of range)
            limit_price = next_low + 0.10 * day_range
            if limit_price <= next_open:  # Order fills if we get the dip
                entry_price = limit_price
            else:
                continue  # Order not filled — skip

        elif method == "adaptive":
            # IBKR Adaptive: simulated 0.15% improvement over open
            entry_price = next_open * (1 - 0.0015)

        # ── Set stop and target ────────────────────────────────────────────
        stop   = entry_price - STOP_ATR_MULT   * atr
        target = entry_price + TARGET_ATR_MULT  * atr

        if target - entry_price <= 0 or entry_price - stop <= 0:
            continue

        in_trade  = True
        entry_idx = i

    return trades


def analyze_trades(trades: list[dict], method: str) -> dict:
    if not trades:
        return {}

    pnls     = [t["pnl"] for t in trades]
    wins     = [p for p in pnls if p > 0]
    losses   = [p for p in pnls if p <= 0]
    total    = sum(pnls)
    win_rate = len(wins) / len(pnls)
    exp      = total / len(pnls)

    # Sharpe (annualized, assuming ~252 trading days, ~25 trades/year)
    if len(pnls) > 1 and np.std(pnls) > 0:
        sharpe = (np.mean(pnls) / np.std(pnls)) * np.sqrt(len(pnls))
    else:
        sharpe = 0

    # Max drawdown
    equity = CAPITAL
    peak   = CAPITAL
    max_dd = 0
    for p in pnls:
        equity = max(0, equity + p)
        peak   = max(peak, equity)
        dd     = (peak - equity) / peak
        max_dd = max(max_dd, dd)

    final_equity  = CAPITAL + total
    total_return  = total / CAPITAL
    profit_factor = abs(sum(wins)) / abs(sum(losses)) if losses else 999

    return {
        "method":        method,
        "n_trades":      len(trades),
        "win_rate":      win_rate,
        "expectancy":    exp,
        "total_pnl":     total,
        "total_return":  total_return,
        "profit_factor": profit_factor,
        "sharpe":        sharpe,
        "max_dd":        max_dd,
        "final_equity":  final_equity,
    }


def run_all():
    console.print("\n[bold cyan]═══════════════════════════════════════════════[/bold cyan]")
    console.print("[bold cyan]    EXECUTION METHOD COMPARISON BACKTEST[/bold cyan]")
    console.print("[bold cyan]═══════════════════════════════════════════════[/bold cyan]")
    console.print(f"[dim]Universe: {UNIVERSE} | {START_DATE} → {END_DATE}[/dim]\n")

    methods = {
        "Baseline (Close)"  : "close",
        "Realistic (Open)"  : "open",
        "Limit Dip Order"   : "limit",
        "IBKR Adaptive"     : "adaptive",
    }

    all_results = {m: [] for m in methods}

    # Run all methods per ticker
    for ticker in UNIVERSE:
        console.print(f"[cyan]Fetching {ticker}...[/cyan]")
        df = get_data(ticker)

        for label, method in methods.items():
            trades = simulate_execution(df, method)
            all_results[label].extend(trades)

    # Analyze combined results
    console.print("\n[bold]Analyzing results...[/bold]\n")

    results = []
    baseline_pnl = None

    for label, trades in all_results.items():
        stats = analyze_trades(trades, label)
        if stats:
            results.append((label, stats))
            if label == "Baseline (Close)":
                baseline_pnl = stats["total_pnl"]

    # Print comparison table
    table = Table(
        title="📊 Execution Method Comparison (All Tickers Combined)",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Method",         style="bold white", width=22)
    table.add_column("Trades",         justify="right", width=8)
    table.add_column("Win%",           justify="right", width=8)
    table.add_column("Expectancy",     justify="right", width=12)
    table.add_column("Total P&L",      justify="right", width=12)
    table.add_column("vs Baseline",    justify="right", width=12)
    table.add_column("Profit Factor",  justify="right", width=14)
    table.add_column("Sharpe",         justify="right", width=8)
    table.add_column("Max DD",         justify="right", width=8)

    for label, s in results:
        vs_base = s["total_pnl"] - baseline_pnl if baseline_pnl else 0
        vs_color = "green" if vs_base >= 0 else "red"
        vs_str   = f"[{vs_color}]{vs_base:+,.0f}[/{vs_color}]"

        win_color   = "green" if s["win_rate"] > 0.52 else "yellow" if s["win_rate"] > 0.45 else "red"
        sharpe_color= "green" if s["sharpe"] > 3 else "yellow" if s["sharpe"] > 1.5 else "red"
        dd_color    = "green" if s["max_dd"] < 0.03 else "yellow" if s["max_dd"] < 0.07 else "red"

        table.add_row(
            label,
            str(s["n_trades"]),
            f"[{win_color}]{s['win_rate']:.1%}[/{win_color}]",
            f"${s['expectancy']:.2f}",
            f"${s['total_pnl']:,.0f}",
            vs_str,
            f"{s['profit_factor']:.2f}",
            f"[{sharpe_color}]{s['sharpe']:.2f}[/{sharpe_color}]",
            f"[{dd_color}]{s['max_dd']:.1%}[/{dd_color}]",
        )

    console.print(table)

    # Print verdict
    best = max(results, key=lambda x: x[1]["total_pnl"])
    worst = min(results, key=lambda x: x[1]["total_pnl"])
    improvement = best[1]["total_pnl"] - results[0][1]["total_pnl"]

    console.print(f"\n[bold green]✅ Best Method: {best[0]}[/bold green]")
    console.print(f"[bold red]❌ Worst Method: {worst[0]}[/bold red]")
    console.print(f"[bold]💰 Best vs Baseline: ${improvement:+,.0f} extra over {START_DATE}–{END_DATE}[/bold]")
    console.print(f"[dim]   (~${improvement/5:,.0f}/year extra on $25,000 capital)[/dim]")

    # Per-ticker breakdown for best method
    console.print(f"\n[bold cyan]Per-Ticker Breakdown — {best[0]}[/bold cyan]")
    ticker_table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    ticker_table.add_column("Ticker")
    ticker_table.add_column("Trades", justify="right")
    ticker_table.add_column("Win%",   justify="right")
    ticker_table.add_column("P&L",    justify="right")
    ticker_table.add_column("Exp/Trade", justify="right")

    best_method_key = best[0]
    _, best_method_code = list(methods.items())[list(methods.keys()).index(best_method_key)]

    for ticker in UNIVERSE:
        df     = get_data(ticker)
        trades = simulate_execution(df, best_method_code)
        if trades:
            pnls = [t["pnl"] for t in trades]
            wr   = sum(1 for p in pnls if p > 0) / len(pnls)
            ticker_table.add_row(
                ticker,
                str(len(trades)),
                f"{wr:.1%}",
                f"${sum(pnls):,.0f}",
                f"${sum(pnls)/len(pnls):.2f}",
            )

    console.print(ticker_table)
    console.print("\n[dim]Ready to implement winning method in production? Run: make paper[/dim]")


if __name__ == "__main__":
    run_all()
