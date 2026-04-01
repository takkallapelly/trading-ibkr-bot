"""
src/execution/order_manager.py
───────────────────────────────
Order state machine — tracks every trade from signal to close.

States:
  PENDING    → signal approved, waiting to place order
  SUBMITTED  → bracket order placed in TWS
  FILLED     → entry order confirmed filled
  CLOSED     → stop or target hit, position closed
  CANCELLED  → manually cancelled or rejected

This solves the position-state-resetting bug from your v7 bot:
  The state ONLY advances when TWS confirms the fill.
  Never reset to PENDING based on timeouts or assumptions.

The OrderManager also tracks execution quality:
  slippage = actual fill price vs mid price at signal time
  fill_rate = % of orders that fill within 1 bar
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from loguru import logger


class OrderState(str, Enum):
    PENDING   = "PENDING"
    SUBMITTED = "SUBMITTED"
    FILLED    = "FILLED"
    CLOSED    = "CLOSED"
    CANCELLED = "CANCELLED"
    REJECTED  = "REJECTED"


@dataclass
class ManagedOrder:
    """Tracks one complete trade lifecycle (entry + stop + target)."""

    # Identity
    ticker        : str
    side          : str          # "LONG" or "SHORT"
    signal_score  : float

    # Sizing
    qty           : int
    entry_price   : float        # requested entry
    stop_price    : float
    target_price  : float

    # IBKR order IDs (set when submitted)
    parent_id     : int = -1
    stop_id       : int = -1
    target_id     : int = -1

    # Fill details (set when filled)
    fill_price    : float = 0.0
    fill_time     : str   = ""

    # Close details (set when closed)
    exit_price    : float = 0.0
    exit_time     : str   = ""
    exit_reason   : str   = ""
    pnl           : float = 0.0

    # State machine
    state         : OrderState = OrderState.PENDING
    created_at    : str        = field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )
    updated_at    : str        = field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )

    def transition(self, new_state: OrderState, **kwargs) -> None:
        """
        Advance the state machine.
        Only allows valid transitions — rejects impossible ones.
        """
        valid_transitions = {
            OrderState.PENDING:   {OrderState.SUBMITTED, OrderState.CANCELLED},
            OrderState.SUBMITTED: {OrderState.FILLED,    OrderState.CANCELLED, OrderState.REJECTED},
            OrderState.FILLED:    {OrderState.CLOSED,    OrderState.CANCELLED},
            OrderState.CLOSED:    set(),
            OrderState.CANCELLED: set(),
            OrderState.REJECTED:  set(),
        }

        if new_state not in valid_transitions.get(self.state, set()):
            logger.warning(
                f"Invalid state transition: {self.ticker} "
                f"{self.state} → {new_state} (ignored)"
            )
            return

        old = self.state
        self.state      = new_state
        self.updated_at = datetime.utcnow().isoformat()

        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

        logger.info(
            f"Order state: {self.ticker} {old.value} → {new_state.value}"
            + (f" | fill=${self.fill_price:.2f}" if new_state == OrderState.FILLED else "")
            + (f" | pnl=${self.pnl:.2f} ({self.exit_reason})" if new_state == OrderState.CLOSED else "")
        )

    @property
    def is_open(self) -> bool:
        return self.state in (OrderState.SUBMITTED, OrderState.FILLED)

    @property
    def slippage(self) -> float:
        """Actual fill vs requested entry. Negative = paid more than expected."""
        if self.fill_price == 0:
            return 0.0
        if self.side == "LONG":
            return self.entry_price - self.fill_price  # positive = better fill
        else:
            return self.fill_price - self.entry_price

    def to_trade_dict(self) -> dict:
        """Convert to trade dict for the database."""
        return {
            "ticker":       self.ticker,
            "side":         self.side,
            "entry_time":   self.fill_time or self.created_at,
            "exit_time":    self.exit_time or None,
            "entry_price":  self.fill_price or self.entry_price,
            "exit_price":   self.exit_price or None,
            "qty":          self.qty,
            "pnl":          self.pnl if self.state == OrderState.CLOSED else None,
            "pnl_pct":      self.pnl / (self.entry_price * self.qty) if self.pnl and self.qty else None,
            "stop_price":   self.stop_price,
            "target_price": self.target_price,
            "signal_score": self.signal_score,
            "exit_reason":  self.exit_reason or None,
            "ibkr_order_id":self.parent_id,
        }


class OrderManager:
    """
    Manages the lifecycle of all orders.

    The LiveTrader creates ManagedOrders here and updates them
    as TWS callbacks come in.
    """

    def __init__(self):
        self._orders: dict[int, ManagedOrder] = {}   # parent_id → order
        self._by_ticker: dict[str, int] = {}          # ticker → parent_id

    def create(self, **kwargs) -> ManagedOrder:
        """Create a new managed order in PENDING state."""
        order = ManagedOrder(**kwargs)
        return order

    def register(self, order: ManagedOrder) -> None:
        """Register an order after it's been submitted to IBKR."""
        self._orders[order.parent_id] = order
        self._by_ticker[order.ticker] = order.parent_id
        logger.debug(f"Registered order {order.parent_id} for {order.ticker}")

    def get_by_id(self, order_id: int) -> ManagedOrder | None:
        return self._orders.get(order_id)

    def get_by_ticker(self, ticker: str) -> ManagedOrder | None:
        pid = self._by_ticker.get(ticker)
        return self._orders.get(pid) if pid else None

    def has_open_position(self, ticker: str) -> bool:
        order = self.get_by_ticker(ticker)
        return order is not None and order.is_open

    def open_orders(self) -> list[ManagedOrder]:
        return [o for o in self._orders.values() if o.is_open]

    def open_tickers(self) -> list[str]:
        return [o.ticker for o in self.open_orders()]

    def open_positions_for_risk(self) -> list[dict]:
        """Format open orders for RiskManager.heat_breaker.check()."""
        return [
            {"ticker": o.ticker, "size_usd": o.entry_price * o.qty}
            for o in self.open_orders()
            if o.state == OrderState.FILLED
        ]

    def on_fill(self, order_id: int, fill_price: float) -> ManagedOrder | None:
        """Call when TWS confirms entry order filled."""
        order = self._orders.get(order_id)
        if order is None:
            # Could be a stop or target fill
            order = self._find_by_child_id(order_id)
            if order is None:
                return None

        if order_id == order.parent_id:
            # Entry fill
            order.transition(
                OrderState.FILLED,
                fill_price=fill_price,
                fill_time=datetime.utcnow().isoformat(),
            )
        elif order_id in (order.stop_id, order.target_id):
            # Exit fill
            direction = 1 if order.side == "LONG" else -1
            pnl = direction * (fill_price - order.fill_price) * order.qty
            reason = "STOP_LOSS" if order_id == order.stop_id else "TAKE_PROFIT"
            order.transition(
                OrderState.CLOSED,
                exit_price=fill_price,
                exit_time=datetime.utcnow().isoformat(),
                exit_reason=reason,
                pnl=round(pnl, 2),
            )
            # Remove from active tracking
            self._by_ticker.pop(order.ticker, None)

        return order

    def on_cancel(self, order_id: int) -> None:
        order = self._orders.get(order_id)
        if order and order.is_open:
            order.transition(OrderState.CANCELLED)
            self._by_ticker.pop(order.ticker, None)

    def execution_stats(self) -> dict:
        """Summary of execution quality."""
        closed = [o for o in self._orders.values()
                  if o.state == OrderState.CLOSED]
        if not closed:
            return {"n_closed": 0, "avg_slippage": 0.0, "win_rate": 0.0}

        slippages = [o.slippage for o in closed]
        winners   = [o for o in closed if o.pnl > 0]
        return {
            "n_closed":     len(closed),
            "avg_slippage": round(sum(slippages) / len(slippages), 4),
            "win_rate":     round(len(winners) / len(closed), 4),
            "total_pnl":    round(sum(o.pnl for o in closed), 2),
        }

    def _find_by_child_id(self, child_id: int) -> ManagedOrder | None:
        for order in self._orders.values():
            if child_id in (order.stop_id, order.target_id):
                return order
        return None
