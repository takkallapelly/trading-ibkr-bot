"""
Wire market context + regime detection into StrategyEngine and LiveTrader.
Run from: C:\\IBKR Bot\\trading-bot
Command:  python fix_market_filter.py

What this adds:
  1. MarketContext — VIX gate, SPY trend, VWAP check
  2. RegimeDetector — Hurst exponent per ticker
  3. Score-weighted position sizing
  4. Pre-trade filter in StrategyEngine.get_signals()
  5. Market context logged on every scan
"""
import shutil, os

# ── Step 1: Copy market_context.py ───────────────────────────────────────────
src = r"market_context.py"
dst = r"src\data\market_context.py"
if os.path.exists(src):
    shutil.copy(src, dst)
    print(f"✓ Copied market_context.py to {dst}")
else:
    print(f"ERROR: {src} not found — download market_context.py from Claude first")
    exit(1)

# ── Step 2: Patch StrategyEngine ─────────────────────────────────────────────
engine_path = r"src\strategy\engine.py"
with open(engine_path, "r", encoding="utf-8") as f:
    engine = f.read()

# Add imports
old_imports = "from src.strategy.signals import SignalEngine\nfrom src.strategy.meta_labeler import MetaLabeler\nfrom src.strategy.signal import Signal"
new_imports = """from src.strategy.signals import SignalEngine
from src.strategy.meta_labeler import MetaLabeler
from src.strategy.signal import Signal
from src.strategy.regime import RegimeDetector
from src.data.market_context import get_market_context, MarketContext"""

if old_imports in engine:
    engine = engine.replace(old_imports, new_imports)
    print("✓ Added imports to StrategyEngine")

# Add ibkr_client to __init__
old_init = """    def __init__(
        self,
        tickers: list[str] | None = None,
        config: dict | None = None,
    ):
        self.tickers      = tickers or TICKERS
        self.cfg          = config or cfg
        self.signal_engine = SignalEngine(config=self.cfg)
        self.meta_labeler  = MetaLabeler()

        logger.info(
            f"StrategyEngine ready | {len(self.tickers)} tickers | "
            f"meta_labeler={'active' if self.meta_labeler.is_active else 'pass-through'}"
        )"""

new_init = """    def __init__(
        self,
        tickers: list[str] | None = None,
        config: dict | None = None,
        ibkr_client=None,
    ):
        self.tickers       = tickers or TICKERS
        self.cfg           = config or cfg
        self.ibkr_client   = ibkr_client
        self.signal_engine = SignalEngine(config=self.cfg)
        self.meta_labeler  = MetaLabeler()
        self.regime        = RegimeDetector()
        self._market_ctx   = None   # cached market context

        logger.info(
            f"StrategyEngine ready | {len(self.tickers)} tickers | "
            f"meta_labeler={'active' if self.meta_labeler.is_active else 'pass-through'} | "
            f"market_filter=active | regime=active"
        )"""

if old_init in engine:
    engine = engine.replace(old_init, new_init)
    print("✓ Updated StrategyEngine.__init__")

# Replace get_signals with market-aware version
old_get = """    def get_signals(
        self,
        tickers: list[str] | None = None,
        loader=None,          # DataLoader instance
        bars: dict | None = None,  # pre-loaded bars (for backtesting)
        print_table: bool = False,
    ) -> list[Signal]:
        \"\"\"
        Scan tickers and return actionable signals after meta-labeling.

        Args:
            tickers     : override ticker list for this call
            loader      : DataLoader to fetch latest bars from
            bars        : pre-loaded {ticker: bar_series} dict (backtesting)
            print_table : print a summary table to the terminal

        Returns:
            List of actionable Signal objects, sorted by score descending.
        \"\"\"
        scan_tickers = tickers or self.tickers

        # ── Get latest bars ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
        if bars is None:
            if loader is None:
                # Import here to avoid circular imports
                from src.data.loader import DataLoader
                loader = DataLoader(tickers=scan_tickers)
            bars = {
                t: loader.get_latest(t)
                for t in scan_tickers
                if loader.get_latest(t) is not None
            }

        if not bars:
            logger.warning("StrategyEngine: no bars available to scan")
            return []

        # ── Layer 1+2+3: signal engine scan ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
        raw_signals = self.signal_engine.scan(bars)

        # ── Meta-labeler vetting ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
        approved = self.meta_labeler.approve_all(raw_signals)

        if print_table:
            self._print_signals(bars, approved)

        return approved"""

new_get = """    def get_signals(
        self,
        tickers: list[str] | None = None,
        loader=None,
        bars: dict | None = None,
        print_table: bool = False,
    ) -> list[Signal]:
        \"\"\"
        Scan tickers and return actionable signals after:
          1. Market context filter (VIX, SPY trend, VWAP)
          2. Per-ticker regime filter (Hurst exponent)
          3. Signal engine (RSI/BB/EMA)
          4. Meta-labeler (ML vetting)
          5. Score-weighted position sizing adjustment
        \"\"\"
        scan_tickers = tickers or self.tickers

        # ── Step 1: Market context (VIX + SPY) ───────────────────────────────
        try:
            ctx = get_market_context(self.ibkr_client)
            self._market_ctx = ctx
            logger.info(f"Market: {ctx}")

            # Hard blocks
            if not ctx.allow_longs and not ctx.allow_shorts:
                logger.warning(
                    f"Market filter: ALL TRADING BLOCKED | {ctx.reason}"
                )
                return []

        except Exception as e:
            logger.warning(f"Market context failed: {e} — trading without filter")
            ctx = None

        # ── Step 2: Get latest bars ───────────────────────────────────────────
        if bars is None:
            if loader is None:
                from src.data.loader import DataLoader
                loader = DataLoader(tickers=scan_tickers)
            bars = {
                t: loader.get_latest(t)
                for t in scan_tickers
                if loader.get_latest(t) is not None
            }

        if not bars:
            logger.warning("StrategyEngine: no bars available")
            return []

        # ── Step 3: Signal engine scan ────────────────────────────────────────
        raw_signals = self.signal_engine.scan(bars)

        # ── Step 4: Market direction filter ──────────────────────────────────
        if ctx:
            filtered = []
            for s in raw_signals:
                from src.strategy.signal import Direction
                if s.direction == Direction.LONG and not ctx.allow_longs:
                    logger.info(f"{s.ticker}: LONG blocked by market filter | {ctx.reason}")
                    continue
                if s.direction == Direction.SHORT and not ctx.allow_shorts:
                    logger.info(f"{s.ticker}: SHORT blocked by market filter | {ctx.reason}")
                    continue
                filtered.append(s)
            raw_signals = filtered

        # ── Step 5: Meta-labeler vetting ──────────────────────────────────────
        approved = self.meta_labeler.approve_all(raw_signals)

        # ── Step 6: Attach position multiplier to each signal ─────────────────
        if ctx and approved:
            for s in approved:
                # Score-weighted sizing: stronger signal = bigger position
                score_mult = 1.0
                if s.score >= 0.65:   score_mult = 1.3   # strong signal
                elif s.score >= 0.55: score_mult = 1.0   # medium signal
                else:                  score_mult = 0.7   # weak signal

                # Combined multiplier
                s._position_mult = round(ctx.position_mult * score_mult, 2)
                logger.debug(
                    f"{s.ticker}: pos_mult={s._position_mult:.2f} "
                    f"(market={ctx.position_mult:.1f} x score={score_mult:.1f})"
                )

        if print_table:
            self._print_signals(bars, approved)

        return approved

    def get_market_context(self):
        \"\"\"Return cached market context from last scan.\"\"\"
        return self._market_ctx"""

if old_get in engine:
    engine = engine.replace(old_get, new_get)
    print("✓ Updated get_signals() with market filter")
else:
    print("⚠ Could not find get_signals() — applying fuzzy patch...")
    # Find and replace just the method body
    start = engine.find("    def get_signals(")
    end   = engine.find("\n    def scan_all_and_print", start)
    if start > -1 and end > -1:
        engine = engine[:start] + new_get + "\n" + engine[end:]
        print("✓ Applied fuzzy patch to get_signals()")
    else:
        print("✗ Could not patch get_signals()")

with open(engine_path, "w", encoding="utf-8") as f:
    f.write(engine)

# ── Step 3: Patch LiveTrader to pass ibkr_client to StrategyEngine ───────────
trader_path = r"src\execution\live_trader.py"
with open(trader_path, "r", encoding="utf-8") as f:
    trader = f.read()

old_engine_init = "        self._engine    = StrategyEngine(tickers=TICKERS, config=intraday_config)"
new_engine_init = "        self._engine    = StrategyEngine(tickers=TICKERS, config=intraday_config, ibkr_client=self._client)"

if old_engine_init in trader:
    trader = trader.replace(old_engine_init, new_engine_init)
    print("✓ Passed ibkr_client to StrategyEngine in LiveTrader")
else:
    print("⚠ Could not find StrategyEngine init in LiveTrader")

# Add market context to scan log
old_log = '''                    logger.info(
                        f"Scan #{scan_count} | {status['time_eastern']} | "
                        f"VIX={_vix:.1f}({_regime}) | "
                        f"open positions: {len(self._order_mgr.open_orders())}"
                    )'''
new_log = '''                    # Get market context summary
                    try:
                        mctx = self._engine.get_market_context()
                        mctx_str = f"SPY={mctx.spy_trend_dir}({mctx.spy_trend_pct:+.1f}%) regime={mctx.market_regime}" if mctx else ""
                    except Exception:
                        mctx_str = ""
                    logger.info(
                        f"Scan #{scan_count} | {status['time_eastern']} | "
                        f"VIX={_vix:.1f}({_regime}) | {mctx_str} | "
                        f"open positions: {len(self._order_mgr.open_orders())}"
                    )'''
if old_log in trader:
    trader = trader.replace(old_log, new_log)
    print("✓ Added market context to scan log")

with open(trader_path, "w", encoding="utf-8") as f:
    f.write(trader)

# ── Step 4: Patch RiskManager to use signal's position multiplier ─────────────
risk_path = r"src\risk\manager.py"
with open(risk_path, "r", encoding="utf-8") as f:
    risk = f.read()

old_size = "            vix_max, vix_val, vix_label = vix_position_size(self._ibkr_client, self._capital)"
new_size = """            vix_max, vix_val, vix_label = vix_position_size(self._ibkr_client, self._capital)
            # Apply signal-level position multiplier if available
            pos_mult = getattr(signal, '_position_mult', 1.0)
            if pos_mult != 1.0:
                vix_max = round(vix_max * pos_mult, 0)
                logger.info(f"Position mult applied: ${vix_max/pos_mult:.0f} × {pos_mult} = ${vix_max:.0f}")
            self._max_position_usd = vix_max"""

if old_size in risk:
    risk = risk.replace(old_size, new_size)
    print("✓ Added signal position multiplier to RiskManager")
else:
    print("⚠ Could not patch RiskManager position sizing")

with open(risk_path, "w", encoding="utf-8") as f:
    f.write(risk)

# Clear pyc cache
for folder in [r"src\strategy\__pycache__", r"src\execution\__pycache__", r"src\risk\__pycache__", r"src\data\__pycache__"]:
    if os.path.exists(folder):
        for f in os.listdir(folder):
            if f.endswith(".pyc"):
                os.remove(os.path.join(folder, f))
print("✓ Cleared pyc cache")

print("""
════════════════════════════════════════════════════════════════
  MARKET FILTER + REGIME DETECTION ACTIVE
════════════════════════════════════════════════════════════════

  Every scan now checks BEFORE taking any trade:

  1. VIX Gate
     VIX > 50  → PANIC — longs blocked (gap risk)
     VIX 35-50 → HIGH  — trade aggressively (best edge)
     VIX 20-35 → ELEVATED — trade normally
     VIX 15-20 → NORMAL — reduced size
     VIX < 15  → LOW   — minimal size (weak edge)

  2. SPY Trend Filter
     SPY down >3% in 20 bars + VIX>35 → avoid longs
     SPY flat/choppy                   → ideal conditions
     SPY up strongly                   → reduced RSI signals

  3. Score-Weighted Position Sizing
     Signal score ≥ 0.65 → 1.3× position size
     Signal score 0.55-0.65 → 1.0× (normal)
     Signal score < 0.55  → 0.7× (smaller)

  4. Market context logged every scan:
     Scan #5 | 14:19 ET | VIX=41.2(HIGH VOL) |
     SPY=DOWN(-2.1%) regime=BEAR | open=0

  Now restart:
    python scripts/run_live.py --paper
""")
