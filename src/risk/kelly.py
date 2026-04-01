"""
src/risk/kelly.py
──────────────────
Kelly Criterion position sizing — the mathematically optimal
bet size given your edge (win rate + win/loss ratio).

Theory (Mark Douglas — Trading in the Zone + Ernie Chan — Quant Trading):
  Full Kelly = W - (L / R)
    W = win rate
    L = loss rate (1 - W)
    R = average win / average loss ratio

  Full Kelly is theoretically optimal but causes massive drawdowns
  in practice because your edge estimate is never perfect.

  Half-Kelly = Full Kelly × 0.5
    - Cuts drawdown by ~75%
    - Only reduces long-term growth by ~25%
    - Standard practice in professional trading

Usage:
    sizer = KellySizer(capital=25000)
    size_usd = sizer.position_size(win_rate=0.44, avg_win=150, avg_loss=100)
"""

from __future__ import annotations

from loguru import logger


class KellySizer:
    """
    Calculates optimal position size using half-Kelly criterion.

    Args:
        capital        : total trading capital in USD
        kelly_fraction : safety multiplier (0.5 = half-Kelly)
        max_position_usd : hard cap per position regardless of Kelly
        max_position_pct : max position as fraction of capital
        min_trades       : minimum trades needed before Kelly is trusted
    """

    def __init__(
        self,
        capital: float = 25_000.0,
        kelly_fraction: float = 0.5,
        max_position_usd: float = 2_500.0,
        max_position_pct: float = 0.10,
        min_trades: int = 20,
    ):
        self.capital          = capital
        self.kelly_fraction   = kelly_fraction
        self.max_position_usd = max_position_usd
        self.max_position_pct = max_position_pct
        self.min_trades       = min_trades

    def position_size_usd(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        trades_count: int = 0,
    ) -> float:
        """
        Calculate optimal position size in USD.

        Args:
            win_rate    : fraction of trades that win (e.g. 0.44)
            avg_win     : average winning trade in USD (e.g. 150.0)
            avg_loss    : average losing trade in USD, positive (e.g. 100.0)
            trades_count: how many trades used to estimate win_rate

        Returns:
            Position size in USD, capped at max_position_usd.
        """
        # Fall back to fixed sizing if not enough trades yet
        if trades_count < self.min_trades:
            default = min(
                self.capital * self.max_position_pct,
                self.max_position_usd,
            )
            logger.debug(
                f"Kelly: only {trades_count} trades (need {self.min_trades}) "
                f"— using default ${default:,.0f}"
            )
            return default

        kelly_f = self._full_kelly(win_rate, avg_win, avg_loss)
        half_k  = kelly_f * self.kelly_fraction

        raw_size = half_k * self.capital
        capped   = min(raw_size, self.max_position_usd,
                       self.capital * self.max_position_pct)
        size     = max(0.0, capped)

        logger.debug(
            f"Kelly: win_rate={win_rate:.1%} avg_win=${avg_win:.0f} "
            f"avg_loss=${avg_loss:.0f} → full_kelly={kelly_f:.3f} "
            f"half_kelly={half_k:.3f} → ${size:,.0f}"
        )
        return size

    def shares_to_buy(
        self,
        position_size_usd: float,
        entry_price: float,
        risk_per_share: float,
    ) -> int:
        """
        Convert dollar position size to number of shares.

        Uses risk-based sizing as a secondary check:
        shares = position_size_usd / entry_price
        but also check: shares × risk_per_share ≤ max_dollar_risk

        Args:
            position_size_usd: from position_size_usd()
            entry_price       : fill price per share
            risk_per_share    : distance from entry to stop in USD

        Returns:
            Number of whole shares to buy (minimum 1).
        """
        if entry_price <= 0:
            return 0

        # Primary: size by dollar allocation
        shares_by_capital = int(position_size_usd / entry_price)

        # Secondary: size by risk (never risk more than max_position_usd)
        if risk_per_share > 0:
            max_risk_usd   = self.max_position_usd * 0.10  # max 10% of pos as risk
            shares_by_risk = int(max_risk_usd / risk_per_share)
            shares = min(shares_by_capital, shares_by_risk)
        else:
            shares = shares_by_capital

        return max(1, shares)

    # ── Private ───────────────────────────────────────────────────────────────

    def _full_kelly(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
    ) -> float:
        """
        Full Kelly fraction.
        f* = W - (L / R)
        where R = avg_win / avg_loss
        """
        if avg_loss <= 0 or win_rate <= 0:
            return 0.0

        loss_rate      = 1.0 - win_rate
        win_loss_ratio = avg_win / avg_loss
        full_kelly     = win_rate - (loss_rate / win_loss_ratio)

        # Kelly can be negative (no edge) — never size negatively
        return max(0.0, full_kelly)
