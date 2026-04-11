"""
scripts/morning_brief.py
────────────────────────
Pre-market intelligence brief. Run at 9:25 AM ET every trading day.

CEO-level synthesis of:
  Agent 2: Futures & Global Markets (NQ/ES/VIX/DXY proxies)
  Agent 5: Key Level Calculator (S/R, VWAP, gaps, max pain proxy)
  Agent 7: Synthesis → directional bias per ticker

Usage:
    python scripts/morning_brief.py

Output: Clean pre-market brief with bias, levels, and catalysts.
"""

import sys
import os
from pathlib import Path
from datetime import datetime, date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

import yfinance as yf
import pandas as pd
import numpy as np
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich import box

console = Console()

UNIVERSE = ["AVGO", "META", "MSFT", "COST"]

FUTURES_PROXIES = {
    "NQ (Nasdaq)":  "QQQ",   # Nasdaq proxy
    "ES (S&P500)":  "SPY",   # S&P proxy
    "VIX":          "^VIX",  # Fear gauge
    "DXY (Dollar)": "UUP",   # Dollar proxy
    "Bonds (10Y)":  "TLT",   # Treasury proxy
    "Gold":         "GLD",   # Risk-off indicator
}

EARNINGS_CALENDAR = {
    # Add upcoming earnings manually — check earningswhispers.com
    # Format: "TICKER": "YYYY-MM-DD"
}


# ── Agent 2: Futures & Global Markets ─────────────────────────────────────────

def get_market_regime() -> dict:
    """Read global market pulse from futures proxies."""
    results = {}
    
    for name, ticker in FUTURES_PROXIES.items():
        try:
            data = yf.download(ticker, period="5d", interval="1d", 
                             progress=False, auto_adjust=True)
            if data.empty or len(data) < 2:
                continue

            prev_close = float(data["Close"].iloc[-2])
            last_close = float(data["Close"].iloc[-1])
            pct_change = (last_close - prev_close) / prev_close * 100

            results[name] = {
                "ticker":     ticker,
                "close":      last_close,
                "prev_close": prev_close,
                "pct_change": pct_change,
            }
        except Exception:
            pass

    return results


def interpret_regime(regime: dict) -> tuple[str, str, int]:
    """
    Returns (bias, reason, confidence_boost).
    bias: BULLISH / BEARISH / NEUTRAL
    """
    signals = []

    # NQ direction
    nq = regime.get("NQ (Nasdaq)", {})
    if nq:
        if nq["pct_change"] > 0.5:
            signals.append(("BULL", f"NQ +{nq['pct_change']:.1f}%"))
        elif nq["pct_change"] < -0.5:
            signals.append(("BEAR", f"NQ {nq['pct_change']:.1f}%"))
        else:
            signals.append(("NEUTRAL", f"NQ flat {nq['pct_change']:+.1f}%"))

    # VIX level
    vix = regime.get("VIX", {})
    if vix:
        v = vix["close"]
        chg = vix["pct_change"]
        if v < 15:
            signals.append(("BULL", f"VIX low ({v:.1f}) — complacent market"))
        elif v > 25:
            signals.append(("BEAR", f"VIX elevated ({v:.1f}) — fear"))
        if chg < -3:
            signals.append(("BULL", f"VIX falling {chg:.1f}%"))
        elif chg > 5:
            signals.append(("BEAR", f"VIX spiking +{chg:.1f}%"))

    # DXY direction (strong dollar = bad for tech)
    dxy = regime.get("DXY (Dollar)", {})
    if dxy:
        if dxy["pct_change"] > 0.3:
            signals.append(("BEAR", f"Dollar strengthening +{dxy['pct_change']:.1f}%"))
        elif dxy["pct_change"] < -0.3:
            signals.append(("BULL", f"Dollar weakening {dxy['pct_change']:.1f}%"))

    # Bonds (TLT up = yields down = good for growth/tech)
    bonds = regime.get("Bonds (10Y)", {})
    if bonds:
        if bonds["pct_change"] > 0.5:
            signals.append(("BULL", f"Bonds up (yields falling) {bonds['pct_change']:+.1f}%"))
        elif bonds["pct_change"] < -0.5:
            signals.append(("BEAR", f"Bonds down (yields rising) {bonds['pct_change']:.1f}%"))

    bull = sum(1 for s, _ in signals if s == "BULL")
    bear = sum(1 for s, _ in signals if s == "BEAR")

    if bull > bear + 1:
        bias = "🟢 BULLISH"
        confidence_boost = min(bull * 5, 20)
    elif bear > bull + 1:
        bias = "🔴 BEARISH"
        confidence_boost = -min(bear * 5, 20)
    else:
        bias = "🟡 NEUTRAL"
        confidence_boost = 0

    reasons = " | ".join(r for _, r in signals[:4])
    return bias, reasons, confidence_boost


# ── Agent 5: Level Calculator ──────────────────────────────────────────────────

def calculate_levels(ticker: str) -> dict:
    """Calculate key S/R levels, VWAP proxy, and gap levels."""
    try:
        data = yf.download(ticker, period="60d", interval="1d",
                          progress=False, auto_adjust=True)
        if data.empty or len(data) < 10:
            return {}

        # Previous day's OHLC
        prev      = data.iloc[-2].squeeze()
        today     = data.iloc[-1].squeeze()
        prev_high = float(prev["High"])
        prev_low  = float(prev["Low"])
        prev_close= float(prev["Close"])
        last_close= float(today["Close"])

        # Pivot points (classic floor trader method)
        pivot = (prev_high + prev_low + prev_close) / 3
        r1    = 2 * pivot - prev_low
        r2    = pivot + (prev_high - prev_low)
        s1    = 2 * pivot - prev_high
        s2    = pivot - (prev_high - prev_low)

        # 20-day VWAP proxy (price × volume / total volume)
        recent = data.tail(20).copy()
        typical_price = (recent["High"] + recent["Low"] + recent["Close"]) / 3
        vwap_proxy = float(
            (typical_price * recent["Volume"]).sum() / recent["Volume"].sum()
        )

        # 52-week range
        year_data  = data.tail(252)
        week52_high = float(year_data["High"].max())
        week52_low  = float(year_data["Low"].min())
        pct_from_high = (last_close - week52_high) / week52_high * 100

        # Gap detection (unfilled gaps in last 20 days)
        gaps = []
        for i in range(1, min(20, len(data))):
            day      = data.iloc[-i]
            prev_day = data.iloc[-i-1]
            gap_up   = float(day["Low"]) - float(prev_day["High"])
            gap_down = float(prev_day["Low"]) - float(day["High"])
            if gap_up > 0.5:
                gaps.append(("GAP UP", float(prev_day["High"]), float(day["Low"])))
            elif gap_down > 0.5:
                gaps.append(("GAP DOWN", float(day["High"]), float(prev_day["Low"])))

        # Trend (EMA 9 vs EMA 20)
        closes  = data["Close"].squeeze()
        ema9    = float(closes.ewm(span=9).mean().iloc[-1])
        ema20   = float(closes.ewm(span=20).mean().iloc[-1])
        trend   = "UPTREND" if ema9 > ema20 else "DOWNTREND"

        # Average True Range (position sizing reference)
        high_low = data["High"] - data["Low"]
        atr = float(high_low.tail(14).mean())
        atr_pct = atr / last_close * 100

        return {
            "last_close":    last_close,
            "prev_close":    prev_close,
            "prev_high":     prev_high,
            "prev_low":      prev_low,
            "pivot":         pivot,
            "r1": r1, "r2": r2,
            "s1": s1, "s2": s2,
            "vwap_proxy":    vwap_proxy,
            "ema9":          ema9,
            "ema20":         ema20,
            "trend":         trend,
            "atr":           atr,
            "atr_pct":       atr_pct,
            "week52_high":   week52_high,
            "week52_low":    week52_low,
            "pct_from_high": pct_from_high,
            "gaps":          gaps[:3],
        }
    except Exception as e:
        console.print(f"[red]Level calc failed for {ticker}: {e}[/red]")
        return {}


def get_ticker_bias(ticker: str, levels: dict, regime_boost: int) -> tuple[str, int, str]:
    """
    Determine per-ticker bias based on levels + regime.
    Returns (bias_label, confidence_pct, reason)
    """
    if not levels:
        return "🟡 NEUTRAL", 50, "insufficient data"

    reasons = []
    score   = 50 + regime_boost

    close = levels["last_close"]
    vwap  = levels["vwap_proxy"]
    trend = levels["trend"]
    pct_from_high = levels["pct_from_high"]

    # Price vs VWAP
    if close > vwap * 1.002:
        score += 8
        reasons.append(f"above VWAP ${vwap:.2f}")
    elif close < vwap * 0.998:
        score -= 8
        reasons.append(f"below VWAP ${vwap:.2f}")

    # EMA trend
    if trend == "UPTREND":
        score += 7
        reasons.append("EMA uptrend")
    else:
        score -= 7
        reasons.append("EMA downtrend")

    # Distance from 52-week high (momentum)
    if pct_from_high > -5:
        score += 5
        reasons.append("near 52w high")
    elif pct_from_high < -20:
        score -= 5
        reasons.append(f"{pct_from_high:.0f}% off 52w high")

    # Price vs pivot
    if close > levels["pivot"]:
        score += 5
        reasons.append("above pivot")
    else:
        score -= 5
        reasons.append("below pivot")

    # Earnings risk (penalize confidence near earnings)
    if ticker in EARNINGS_CALENDAR:
        earnings_date = datetime.strptime(EARNINGS_CALENDAR[ticker], "%Y-%m-%d").date()
        days_to_earnings = (earnings_date - date.today()).days
        if 0 <= days_to_earnings <= 3:
            score -= 15
            reasons.append(f"⚠️ earnings in {days_to_earnings}d")

    score = max(20, min(90, score))

    if score >= 65:
        bias = "🟢 LONG"
    elif score <= 40:
        bias = "🔴 SHORT"
    else:
        bias = "🟡 NEUTRAL"

    return bias, score, " | ".join(reasons[:3])


# ── Agent 7: Synthesis & Output ────────────────────────────────────────────────

def print_regime_table(regime: dict, bias: str, reason: str) -> None:
    table = Table(title="🌍 Global Market Pulse", box=box.ROUNDED, 
                 show_header=True, header_style="bold cyan")
    table.add_column("Indicator", style="white", width=18)
    table.add_column("Last", justify="right", style="yellow")
    table.add_column("Change", justify="right")
    table.add_column("Signal", justify="center")

    for name, data in regime.items():
        chg = data["pct_change"]
        chg_str = f"{chg:+.2f}%"
        chg_color = "green" if chg > 0 else "red" if chg < 0 else "white"
        signal = "↑" if chg > 0.3 else "↓" if chg < -0.3 else "→"
        table.add_row(
            name,
            f"${data['close']:.2f}" if "VIX" not in name else f"{data['close']:.2f}",
            f"[{chg_color}]{chg_str}[/{chg_color}]",
            signal,
        )
    console.print(table)
    console.print(Panel(f"[bold]{bias}[/bold]  |  {reason}", 
                       title="Market Regime", border_style="cyan"))


def print_ticker_brief(ticker: str, levels: dict, bias: str, 
                       confidence: int, reason: str) -> None:
    if not levels:
        return

    close    = levels["last_close"]
    atr      = levels["atr"]
    bias_color = "green" if "LONG" in bias else "red" if "SHORT" in bias else "yellow"

    # Key levels to watch
    key_levels = [
        ("R2 (strong resist)", levels["r2"]),
        ("R1 (resist)",        levels["r1"]),
        ("VWAP",               levels["vwap_proxy"]),
        ("Pivot",              levels["pivot"]),
        ("S1 (support)",       levels["s1"]),
        ("S2 (strong support)",levels["s2"]),
    ]

    # Find immediate levels (within 2×ATR of close)
    nearby = [(n, p) for n, p in key_levels if abs(p - close) < atr * 2]

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Level", style="cyan")
    table.add_column("Price", justify="right", style="yellow")
    table.add_column("Distance", justify="right")
    table.add_column("Type")

    for name, price in sorted(nearby, key=lambda x: x[1], reverse=True):
        dist = price - close
        dist_str = f"{dist:+.2f} ({dist/close*100:+.1f}%)"
        dist_color = "green" if dist > 0 else "red"
        level_type = "🔴 RESIST" if price > close else "🟢 SUPPORT"
        table.add_row(name, f"${price:.2f}", 
                     f"[{dist_color}]{dist_str}[/{dist_color}]", level_type)

    # Gap alert
    gap_str = ""
    if levels["gaps"]:
        g = levels["gaps"][0]
        gap_str = f"\n  📊 Gap: {g[0]} zone ${g[1]:.2f}–${g[2]:.2f}"

    console.print(Panel(
        f"[bold {bias_color}]{bias}[/bold {bias_color}]  |  "
        f"Confidence: {confidence}%  |  Close: ${close:.2f}\n"
        f"ATR: ${atr:.2f} ({levels['atr_pct']:.1f}%)  |  "
        f"Trend: {levels['trend']}  |  "
        f"52w high: {levels['pct_from_high']:+.1f}%\n"
        f"Reason: {reason}{gap_str}",
        title=f"[bold]{ticker}[/bold]",
        border_style=bias_color,
    ))
    console.print(table)


def check_catalysts() -> list[str]:
    """Check for known scheduled catalysts today."""
    catalysts = []
    today_str = date.today().strftime("%Y-%m-%d")

    # Earnings
    for ticker, earnings_date in EARNINGS_CALENDAR.items():
        if earnings_date == today_str:
            catalysts.append(f"⚡ {ticker} EARNINGS TODAY")
        elif earnings_date == (date.today() + timedelta(days=1)).strftime("%Y-%m-%d"):
            catalysts.append(f"⚠️  {ticker} earnings TOMORROW")

    # Day of week warnings
    weekday = date.today().weekday()
    if weekday == 4:  # Friday
        catalysts.append("📅 Friday — watch for position unwinding into close")
    if weekday == 0:  # Monday
        catalysts.append("📅 Monday — weekend gap risk, lower early liquidity")

    return catalysts


def main():
    now = datetime.now()
    console.print(Panel(
        f"[bold cyan]PRE-MARKET INTELLIGENCE BRIEF[/bold cyan]\n"
        f"[white]{now.strftime('%A, %B %d %Y  %H:%M')} ET[/white]\n"
        f"[dim]Universe: {', '.join(UNIVERSE)}[/dim]",
        box=box.DOUBLE,
        border_style="cyan",
    ))

    # Agent 2: Global Markets
    console.print("\n[bold cyan]AGENT 2: GLOBAL MARKET PULSE[/bold cyan]")
    regime = get_market_regime()
    market_bias, market_reason, regime_boost = interpret_regime(regime)
    print_regime_table(regime, market_bias, market_reason)

    # Check catalysts
    catalysts = check_catalysts()
    if catalysts:
        console.print("\n[bold red]⚡ CATALYSTS TODAY[/bold red]")
        for c in catalysts:
            console.print(f"  {c}")

    # Agent 5 + 7: Per-ticker levels and synthesis
    console.print("\n[bold cyan]AGENT 5+7: TICKER LEVELS & BIAS[/bold cyan]")

    summary_table = Table(
        title="📋 Morning Summary", box=box.ROUNDED,
        show_header=True, header_style="bold cyan"
    )
    summary_table.add_column("Ticker",     style="bold white", width=8)
    summary_table.add_column("Bias",       width=12)
    summary_table.add_column("Confidence", justify="center", width=12)
    summary_table.add_column("Close",      justify="right", width=10)
    summary_table.add_column("Key Level",  justify="right", width=12)
    summary_table.add_column("ATR",        justify="right", width=10)
    summary_table.add_column("Reason",     width=35)

    for ticker in UNIVERSE:
        levels = calculate_levels(ticker)
        bias, confidence, reason = get_ticker_bias(ticker, levels, regime_boost)
        print_ticker_brief(ticker, levels, bias, confidence, reason)

        if levels:
            bias_color = "green" if "LONG" in bias else "red" if "SHORT" in bias else "yellow"
            # Nearest level (first resistance or support)
            nearest_level = levels["r1"] if levels["last_close"] < levels["r1"] else levels["s1"]
            summary_table.add_row(
                ticker,
                f"[{bias_color}]{bias}[/{bias_color}]",
                f"[{'green' if confidence > 65 else 'yellow' if confidence > 45 else 'red'}]"
                f"{confidence}%[/{'green' if confidence > 65 else 'yellow' if confidence > 45 else 'red'}]",
                f"${levels['last_close']:.2f}",
                f"${nearest_level:.2f}",
                f"${levels['atr']:.2f}",
                reason,
            )

    console.print("\n")
    console.print(summary_table)

    console.print(Panel(
        "[bold]Trading Rules for Today:[/bold]\n"
        "• Only trade signals that AGREE with market regime bias\n"
        "• Skip trades if VIX > 30 (too much noise)\n"
        "• First 15 min (9:30–9:45): observe only, no entries\n"
        "• Last 15 min (3:45–4:00): no new entries\n"
        "• If 3 consecutive losses: STOP for the day\n"
        "• Size = half-Kelly until 50+ trades accumulated",
        title="⚖️  Risk Rules",
        border_style="yellow",
    ))

    console.print(f"\n[dim]Brief generated at {now.strftime('%H:%M:%S')} | "
                 f"Run again at 9:25 AM ET for fresh data[/dim]")


if __name__ == "__main__":
    main()
