"""
src/data/store.py
─────────────────
SQLite database layer. Handles all reads and writes for:
  - OHLCV bars with features (one table per ticker)
  - Trade log (every order placed by the bot)
  - Equity curve (daily P&L snapshots)
  - ML model metadata

Uses pandas + raw SQLite — no ORM complexity.
All methods are safe to call from multiple threads (uses WAL mode).
"""

import sqlite3
from pathlib import Path
from datetime import datetime, date
import pandas as pd
from loguru import logger

from src.config import settings


class DataStore:
    """
    Simple SQLite wrapper for the trading bot.

    Usage:
        store = DataStore()
        store.save_bars("TSLA", df)
        df = store.load_bars("TSLA", start="2024-01-01")
        store.log_trade({...})
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or settings.DATA_DIR / "trading_bot.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Initialise schema ─────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS trades (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker        TEXT    NOT NULL,
                    side          TEXT    NOT NULL,   -- LONG or SHORT
                    entry_time    TEXT    NOT NULL,
                    exit_time     TEXT,
                    entry_price   REAL    NOT NULL,
                    exit_price    REAL,
                    qty           REAL    NOT NULL,
                    pnl           REAL,
                    pnl_pct       REAL,
                    stop_price    REAL,
                    target_price  REAL,
                    signal_score  REAL,
                    exit_reason   TEXT,   -- TAKE_PROFIT, STOP_LOSS, MANUAL, EOD
                    ibkr_order_id INTEGER,
                    created_at    TEXT    DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS equity_curve (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_date TEXT    NOT NULL UNIQUE,
                    equity        REAL    NOT NULL,
                    daily_pnl     REAL,
                    daily_pnl_pct REAL,
                    open_positions INTEGER DEFAULT 0,
                    created_at    TEXT    DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS ml_models (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_date    TEXT    NOT NULL,
                    model_path    TEXT    NOT NULL,
                    accuracy      REAL,
                    n_trades      INTEGER,
                    notes         TEXT,
                    created_at    TEXT    DEFAULT (datetime('now'))
                );

                CREATE INDEX IF NOT EXISTS idx_trades_ticker
                    ON trades(ticker);
                CREATE INDEX IF NOT EXISTS idx_trades_entry_time
                    ON trades(entry_time);
            """)
        logger.debug(f"Database ready: {self.db_path}")

    # ── OHLCV bars ────────────────────────────────────────────────────────────

    def save_bars(self, ticker: str, df: pd.DataFrame) -> None:
        """
        Save feature-enriched OHLCV bars for one ticker.
        Uses INSERT OR REPLACE so re-running never creates duplicates.
        """
        if df.empty:
            logger.warning(f"save_bars({ticker}): empty DataFrame — skipping")
            return

        table = self._bar_table(ticker)
        df_out = df.copy()

        # Store datetime index as string for SQLite compatibility
        df_out.index = df_out.index.astype(str)
        df_out.index.name = "datetime"
        df_out = df_out.reset_index()

        with self._connect() as conn:
            # Create table dynamically to handle any feature columns
            cols = ", ".join(
                f'"{c}" REAL' if c != "datetime" else '"datetime" TEXT PRIMARY KEY'
                for c in df_out.columns
            )
            conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({cols})')
            df_out.to_sql(
                table, conn,
                if_exists="replace",
                index=False,
            )

        logger.info(f"Saved {len(df):,} bars for {ticker} → {table}")

    def load_bars(
        self,
        ticker: str,
        start: str | None = None,
        end: str | None = None,
        columns: list[str] | None = None,
    ) -> pd.DataFrame:
        """
        Load bars for one ticker from the database.

        Args:
            ticker  : e.g. "TSLA"
            start   : "YYYY-MM-DD" filter (optional)
            end     : "YYYY-MM-DD" filter (optional)
            columns : list of column names to load (None = all)

        Returns:
            DataFrame indexed by UTC datetime, or empty DataFrame if not found.
        """
        table = self._bar_table(ticker)
        col_sql = "*" if not columns else ", ".join(f'"{c}"' for c in ["datetime"] + columns)

        query = f'SELECT {col_sql} FROM "{table}"'
        params = []

        if start:
            query += " WHERE datetime >= ?"
            params.append(start)
        if end:
            query += (" AND" if start else " WHERE") + " datetime <= ?"
            params.append(end)
        query += " ORDER BY datetime"

        try:
            with self._connect() as conn:
                df = pd.read_sql_query(query, conn, params=params)
            if df.empty:
                return df
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
            df = df.set_index("datetime")
            return df
        except Exception as e:
            logger.warning(f"load_bars({ticker}): {e}")
            return pd.DataFrame()

    def bar_count(self, ticker: str) -> int:
        """Return number of bars stored for a ticker."""
        table = self._bar_table(ticker)
        try:
            with self._connect() as conn:
                result = conn.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()
                return result[0] if result else 0
        except Exception:
            return 0

    # ── Trade log ─────────────────────────────────────────────────────────────

    def log_trade(self, trade: dict) -> int:
        """
        Insert a new trade record. Returns the new trade ID.

        Required keys: ticker, side, entry_time, entry_price, qty
        Optional keys: exit_time, exit_price, pnl, pnl_pct, stop_price,
                       target_price, signal_score, exit_reason, ibkr_order_id
        """
        cols = ", ".join(trade.keys())
        placeholders = ", ".join("?" for _ in trade)
        sql = f"INSERT INTO trades ({cols}) VALUES ({placeholders})"

        with self._connect() as conn:
            cur = conn.execute(sql, list(trade.values()))
            return cur.lastrowid

    def update_trade(self, trade_id: int, updates: dict) -> None:
        """Update an existing trade (e.g. fill in exit_price and pnl)."""
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        sql = f"UPDATE trades SET {set_clause} WHERE id = ?"
        with self._connect() as conn:
            conn.execute(sql, [*updates.values(), trade_id])

    def load_trades(
        self,
        ticker: str | None = None,
        start: str | None = None,
        closed_only: bool = True,
    ) -> pd.DataFrame:
        """Load trade history as a DataFrame."""
        query = "SELECT * FROM trades WHERE 1=1"
        params = []
        if ticker:
            query += " AND ticker = ?"
            params.append(ticker)
        if start:
            query += " AND entry_time >= ?"
            params.append(start)
        if closed_only:
            query += " AND exit_time IS NOT NULL AND pnl IS NOT NULL"
        query += " ORDER BY entry_time"

        with self._connect() as conn:
            return pd.read_sql_query(query, conn, params=params)

    def open_trades(self) -> pd.DataFrame:
        """Return all currently open positions."""
        with self._connect() as conn:
            return pd.read_sql_query(
                "SELECT * FROM trades WHERE exit_time IS NULL ORDER BY entry_time",
                conn,
            )

    # ── Equity curve ──────────────────────────────────────────────────────────

    def save_equity_snapshot(
        self,
        equity: float,
        daily_pnl: float,
        open_positions: int = 0,
        snapshot_date: str | None = None,
    ) -> None:
        """Save today's equity snapshot."""
        snap_date = snapshot_date or date.today().isoformat()
        daily_pnl_pct = daily_pnl / (equity - daily_pnl) if (equity - daily_pnl) != 0 else 0

        with self._connect() as conn:
            conn.execute("""
                INSERT INTO equity_curve
                    (snapshot_date, equity, daily_pnl, daily_pnl_pct, open_positions)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_date) DO UPDATE SET
                    equity=excluded.equity,
                    daily_pnl=excluded.daily_pnl,
                    daily_pnl_pct=excluded.daily_pnl_pct,
                    open_positions=excluded.open_positions
            """, [snap_date, equity, daily_pnl, daily_pnl_pct, open_positions])

    def load_equity_curve(self, days: int = 90) -> pd.DataFrame:
        """Load equity curve for the last N days."""
        with self._connect() as conn:
            df = pd.read_sql_query(
                """SELECT snapshot_date, equity, daily_pnl, daily_pnl_pct, open_positions
                   FROM equity_curve
                   ORDER BY snapshot_date DESC
                   LIMIT ?""",
                conn, params=[days],
            )
        return df.sort_values("snapshot_date").reset_index(drop=True)

    # ── ML model registry ─────────────────────────────────────────────────────

    def register_model(
        self,
        model_path: str,
        accuracy: float,
        n_trades: int,
        notes: str = "",
    ) -> None:
        """Record a newly trained ML model in the database."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ml_models
                   (model_date, model_path, accuracy, n_trades, notes)
                   VALUES (?, ?, ?, ?, ?)""",
                [date.today().isoformat(), str(model_path), accuracy, n_trades, notes],
            )

    def latest_model_path(self) -> str | None:
        """Return the path of the most recently trained model."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT model_path FROM ml_models ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else None

    # ── Stats helpers ─────────────────────────────────────────────────────────

    def win_rate(self, ticker: str | None = None, lookback_days: int = 30) -> float:
        """Calculate win rate from recent closed trades."""
        trades = self.load_trades(ticker=ticker, closed_only=True)
        if trades.empty:
            return 0.0
        recent = trades.tail(lookback_days * 2)  # approx
        winners = (recent["pnl"] > 0).sum()
        return float(winners / len(recent)) if len(recent) > 0 else 0.0

    def daily_pnl_today(self) -> float:
        """Sum of P&L from all trades closed today."""
        today = date.today().isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE exit_time >= ? AND pnl IS NOT NULL",
                [today],
            ).fetchone()
        return float(row[0]) if row else 0.0

    # ── Private ───────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _bar_table(ticker: str) -> str:
        """Table name for a ticker's bars."""
        return f"bars_{ticker.upper().replace('.', '_')}"
