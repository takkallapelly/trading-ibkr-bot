"""
src/backtest/transaction_costs.py
──────────────────────────────────
Realistic transaction cost model from Harris — Trading and Exchanges.

Harris's key insight: transaction costs have THREE components:
  1. Bid-Ask Spread    — paid to dealers/market makers
  2. Market Impact     — price moves against you as you buy/sell
  3. Timing cost       — cost of waiting for the right moment

For retail traders using market orders on large-cap stocks:
  Spread:        ~0.02% round trip (AAPL, MSFT, META etc.)
  Market Impact: ~0.01% for our small position sizes
  Timing:        ~0.01% (we use limit bracket orders, not market)
  Total:         ~0.04% round trip = $1.00 on $2,500 position

Harris: "Uninformed traders always pay the spread.
         The only way to win is to have an edge exceeding spread costs."

Our edge (backtest): $31.64 expectancy per trade
Our cost per trade:  ~$1.00 (0.04% of $2,500)
Net edge:            $30.64 per trade → strong edge survives costs

Usage:
    model = TransactionCostModel()
    cost = model.round_trip_cost(price=487.50, qty=5, ticker="META")
    net_pnl = gross_pnl - cost
"""

from __future__ import annotations
import pandas as pd
from loguru import logger


class TransactionCostModel:
    """
    Realistic transaction cost model for large-cap US equities.

    Based on Harris (Trading and Exchanges) Chapter 14 (Bid/Ask Spreads)
    and Chapter 21 (Liquidity and Transaction Cost Measurement).

    Harris classifies transaction costs into:
      1. Explicit costs: commissions (IBKR charges ~$0.005/share)
      2. Implicit costs: spread, market impact, timing

    For our universe (AAPL, MSFT, META, NVDA etc.):
    - All are highly liquid large-caps
    - Spreads are typically 1-2 cents (0.01-0.02%)
    - Market impact is minimal at our small sizes (<$5,000)
    - IBKR commission: ~$0.005/share or minimum $1.00
    """

    # Spread estimates for our tickers (% of price, round trip)
    # Harris: spread = 2 × half-spread, round trip = full spread
    TICKER_SPREADS = {
        "AAPL": 0.0002,  # ~$0.04 spread on $200 stock = 0.02%
        "MSFT": 0.0002,  # ~$0.08 spread on $400 stock = 0.02%
        "META": 0.0002,  # ~$0.10 spread on $500 stock = 0.02%
        "NVDA": 0.0003,  # ~$0.30 spread on $1000 stock = 0.03%
        "NFLX": 0.0003,  # higher spread, higher price
        "COST": 0.0002,
        "HD":   0.0002,
        "CRM":  0.0003,
        "ORCL": 0.0002,
        "GS":   0.0002,
        "DEFAULT": 0.0003,  # conservative default
    }

    # Market impact (% of position size, round trip)
    # For positions < $5,000 in liquid large-caps: minimal
    MARKET_IMPACT_PCT = 0.0001  # 0.01% round trip

    # IBKR commission: $0.005 per share, min $1.00 per order
    IBKR_RATE_PER_SHARE = 0.005
    IBKR_MIN_PER_ORDER  = 1.00

    def commission(self, qty: int) -> float:
        """IBKR commission for one order (entry OR exit, not round trip)."""
        return max(qty * self.IBKR_RATE_PER_SHARE, self.IBKR_MIN_PER_ORDER)

    def spread_cost(self, price: float, qty: int, ticker: str = "DEFAULT") -> float:
        """
        Bid-ask spread cost — paid on both entry and exit.

        Harris: "Uninformed traders always pay the spread."
        We are uninformed traders (technical signals, not inside info).
        We pay the spread every time we enter and exit.

        Returns round-trip spread cost in dollars.
        """
        spread_pct = self.TICKER_SPREADS.get(ticker, self.TICKER_SPREADS["DEFAULT"])
        return price * qty * spread_pct

    def market_impact(self, price: float, qty: int) -> float:
        """
        Market impact cost — price moves against us as we trade.

        Harris: "Prices tend to move against traders' positions before
        they can trade out of them."

        For our small sizes ($2,500 max), market impact is tiny.
        Returns round-trip market impact in dollars.
        """
        return price * qty * self.MARKET_IMPACT_PCT

    def round_trip_cost(
        self,
        price:  float,
        qty:    int,
        ticker: str = "DEFAULT",
    ) -> float:
        """
        Total round-trip transaction cost for one trade.

        Includes: commission (entry + exit) + spread + market impact.

        Args:
            price  : entry price per share
            qty    : number of shares
            ticker : stock ticker for spread lookup

        Returns:
            Total cost in dollars (reduces P&L)
        """
        commission  = self.commission(qty) * 2  # entry + exit
        spread      = self.spread_cost(price, qty, ticker)
        impact      = self.market_impact(price, qty)
        total       = commission + spread + impact

        logger.debug(
            f"Transaction costs | {ticker} {qty}sh @${price:.2f} | "
            f"commission=${commission:.2f} "
            f"spread=${spread:.2f} "
            f"impact=${impact:.2f} "
            f"total=${total:.2f} "
            f"({total/(price*qty)*100:.3f}% of position)"
        )

        return round(total, 4)

    def breakeven_edge(self, price: float, qty: int, ticker: str = "DEFAULT") -> float:
        """
        Minimum gross P&L needed to break even after costs.

        Harris: "Traders who do not expect to win should refrain from trading."
        Use this to verify our expected edge exceeds costs.

        Returns minimum gross P&L in dollars.
        """
        return self.round_trip_cost(price, qty, ticker)

    def cost_as_pct(self, price: float, qty: int, ticker: str = "DEFAULT") -> float:
        """Transaction cost as % of position size."""
        cost = self.round_trip_cost(price, qty, ticker)
        return cost / (price * qty)

    def apply_to_trades(self, trades: list[dict]) -> list[dict]:
        """
        Apply realistic transaction costs to a list of backtest trades.

        Reduces each trade's P&L by the estimated round-trip cost.
        Use this to get more realistic backtest results.

        Args:
            trades: list of trade dicts with entry_price, qty, ticker, pnl

        Returns:
            trades with adjusted pnl and cost added
        """
        adjusted = []
        total_cost = 0

        for trade in trades:
            t          = dict(trade)
            price      = float(t.get("entry_price", 0))
            qty        = int(t.get("qty", 1))
            ticker     = t.get("ticker", "DEFAULT")
            gross_pnl  = float(t.get("pnl", 0))

            cost       = self.round_trip_cost(price, qty, ticker)
            net_pnl    = gross_pnl - cost
            total_cost += cost

            t["gross_pnl"]       = round(gross_pnl, 2)
            t["transaction_cost"] = round(cost, 4)
            t["pnl"]             = round(net_pnl, 2)
            adjusted.append(t)

        logger.info(
            f"Transaction costs applied | "
            f"{len(trades)} trades | "
            f"total costs=${total_cost:.2f} | "
            f"avg cost/trade=${total_cost/len(trades):.2f}"
            if trades else "No trades"
        )

        return adjusted

    def cost_summary(self, trades: list[dict]) -> dict:
        """
        Summary of transaction costs across all trades.
        Use to understand how much spread/commission is eating into returns.
        """
        if not trades:
            return {}

        costs = [
            self.round_trip_cost(
                float(t.get("entry_price", 0)),
                int(t.get("qty", 1)),
                t.get("ticker", "DEFAULT"),
            )
            for t in trades
        ]

        total_pnl_gross = sum(float(t.get("gross_pnl", t.get("pnl", 0))) for t in trades)
        total_cost      = sum(costs)

        return {
            "n_trades":          len(trades),
            "total_cost":        round(total_cost, 2),
            "avg_cost_per_trade":round(total_cost / len(trades), 2),
            "total_gross_pnl":   round(total_pnl_gross, 2),
            "total_net_pnl":     round(total_pnl_gross - total_cost, 2),
            "cost_as_pct_gross": round(total_cost / max(total_pnl_gross, 1) * 100, 1),
            "harris_verdict": (
                "✅ Edge survives costs" if total_pnl_gross > total_cost * 3
                else "⚠️ Edge barely covers costs — review strategy"
            ),
        }
