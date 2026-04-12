# ============================================================
# RSI(2) Mean Reversion — QuantConnect Cloud version
# Mirror of the IBKR live bot strategy for backtesting research.
#
# HOW TO USE:
#   1. Go to quantconnect.com → Log in (free account)
#   2. Click "Algorithm Lab" → New Algorithm → Python
#   3. Delete the default code and paste this entire file
#   4. Click "Backtest" — uses QC's 20-year survivorship-bias-free data
#   5. Compare results to your local backtest HTML reports
#
# PARAMETER VARIANTS: See parameter_variants.py for pre-built configs to test.
# ============================================================

from AlgorithmImports import *


# ── Strategy Parameters (edit these to match parameter_variants.py) ─────────
RSI_PERIOD          = 2
RSI_OVERSOLD        = 10      # Long entry threshold
RSI_OVERBOUGHT      = 90      # Short entry threshold (unused in long_only)
RSI_WEIGHT          = 0.50

BB_PERIOD           = 20
BB_STD              = 2.0
BB_WEIGHT           = 0.30

EMA_FAST            = 9
EMA_SLOW            = 20
EMA_WEIGHT          = 0.20

MIN_SCORE           = 0.60    # Composite score threshold to trade
LONG_ONLY           = True    # Match your live bot config
STOP_ATR_MULT       = 1.0     # Stop loss = entry - N×ATR
TARGET_ATR_MULT     = 2.0     # Take profit = entry + N×ATR
ATR_PERIOD          = 14

AVOID_FIRST_MIN     = 15      # Skip first N minutes of session
AVOID_LAST_MIN      = 15      # Skip last N minutes of session
MAX_POSITIONS       = 3       # Max simultaneous open positions
POSITION_PCT        = 0.10    # 10% of portfolio per trade

TICKERS = [
    "MSFT", "META", "JPM", "HD", "GS",
    "V",    "UNH",  "LLY", "PG", "AVGO",
]


class RSIMeanReversion(QCAlgorithm):
    """
    RSI(2) mean-reversion strategy — exact parameter mirror of the IBKR live bot.
    3-layer composite signal: RSI(2) + Bollinger Band + EMA cross.
    """

    def initialize(self) -> None:
        self.set_start_date(2020, 1, 1)
        self.set_end_date(2024, 12, 31)
        self.set_cash(25_000)
        self.set_brokerage_model(BrokerageName.INTERACTIVE_BROKERS_BROKERAGE,
                                  AccountType.MARGIN)

        self._indicators: dict[str, dict] = {}
        self._entry_prices: dict[str, float] = {}

        for ticker in TICKERS:
            equity = self.add_equity(ticker, Resolution.MINUTE)
            equity.set_fee_model(ConstantFeeModel(0.005))   # $0.005/share = IB Tiered

            self._indicators[ticker] = {
                "rsi":      self.RSI(ticker, RSI_PERIOD, MovingAverageType.SIMPLE,
                                     Resolution.MINUTE),
                "bb":       self.BB(ticker, BB_PERIOD, BB_STD, MovingAverageType.SIMPLE,
                                    Resolution.MINUTE),
                "ema_fast": self.EMA(ticker, EMA_FAST, Resolution.MINUTE),
                "ema_slow": self.EMA(ticker, EMA_SLOW, Resolution.MINUTE),
                "atr":      self.ATR(ticker, ATR_PERIOD, MovingAverageType.SIMPLE,
                                     Resolution.MINUTE),
            }

        # SPY for regime filter
        self.add_equity("SPY", Resolution.MINUTE)
        self._spy_rsi = self.RSI("SPY", RSI_PERIOD, MovingAverageType.SIMPLE,
                                  Resolution.MINUTE)

        self.set_warm_up(timedelta(days=30))

    def on_data(self, data: Slice) -> None:
        if self.is_warming_up:
            return

        # Block first / last N minutes of session
        et = self.time
        market_open  = datetime(et.year, et.month, et.day, 9, 30)
        market_close = datetime(et.year, et.month, et.day, 16, 0)
        if et < market_open + timedelta(minutes=AVOID_FIRST_MIN):
            return
        if et > market_close - timedelta(minutes=AVOID_LAST_MIN):
            return

        # SPY regime filter — block longs if SPY is in freefall
        if self._spy_rsi.is_ready and self._spy_rsi.current.value < 30:
            return

        open_count = len([x for x in self.portfolio.values()
                          if x.invested and x.symbol.value in TICKERS])

        for ticker in TICKERS:
            ind = self._indicators[ticker]

            if not all(i.is_ready for i in ind.values()):
                continue

            if not data.bars.contains_key(ticker):
                continue

            bar      = data.bars[ticker]
            close    = bar.close
            rsi_val  = ind["rsi"].current.value
            bb_upper = ind["bb"].upper_band.current.value
            bb_lower = ind["bb"].lower_band.current.value
            ema_fast = ind["ema_fast"].current.value
            ema_slow = ind["ema_slow"].current.value
            atr_val  = ind["atr"].current.value

            if atr_val <= 0:
                continue

            # ── Composite score ───────────────────────────────────────────────
            rsi_sig = 1.0 if rsi_val < RSI_OVERSOLD else (-1.0 if rsi_val > RSI_OVERBOUGHT else 0.0)

            bb_range = bb_upper - bb_lower
            bb_pct   = (close - bb_lower) / bb_range if bb_range > 0 else 0.5
            bb_sig   = 1.0 if bb_pct <= 0.05 else (-1.0 if bb_pct >= 0.95 else 0.0)

            ema_sig = 1.0 if ema_fast > ema_slow else -1.0

            score = (rsi_sig * RSI_WEIGHT) + (bb_sig * BB_WEIGHT) + (ema_sig * EMA_WEIGHT)

            # ── Entry logic ───────────────────────────────────────────────────
            holding = self.portfolio[ticker].invested

            if not holding and score >= MIN_SCORE and open_count < MAX_POSITIONS:
                # Direction consistency: RSI and BB must agree
                if rsi_sig < 0 or bb_sig < 0:
                    continue

                stop   = round(close - STOP_ATR_MULT   * atr_val, 2)
                target = round(close + TARGET_ATR_MULT * atr_val, 2)

                risk   = close - stop
                reward = target - close
                if risk <= 0 or (reward / risk) < STOP_ATR_MULT:
                    continue

                quantity = int((self.portfolio.cash * POSITION_PCT) / close)
                if quantity < 1:
                    continue

                self.market_order(ticker, quantity)
                self._entry_prices[ticker] = close
                open_count += 1

                self.debug(f"ENTRY {ticker} @ {close:.2f} | score={score:.2f} "
                           f"rsi={rsi_val:.1f} | stop={stop:.2f} target={target:.2f}")

            elif holding:
                # ── Exit logic ────────────────────────────────────────────────
                entry  = self._entry_prices.get(ticker, close)
                stop   = entry - STOP_ATR_MULT   * atr_val
                target = entry + TARGET_ATR_MULT * atr_val

                if close <= stop:
                    self.liquidate(ticker)
                    self.debug(f"STOP {ticker} @ {close:.2f}")
                    self._entry_prices.pop(ticker, None)
                    open_count -= 1
                elif close >= target:
                    self.liquidate(ticker)
                    self.debug(f"TARGET {ticker} @ {close:.2f}")
                    self._entry_prices.pop(ticker, None)
                    open_count -= 1

    def on_end_of_day(self, symbol) -> None:
        """Placeholder — uncomment below to close all positions at EOD."""
        pass
        # if self.portfolio[symbol].invested:
        #     self.liquidate(symbol)
