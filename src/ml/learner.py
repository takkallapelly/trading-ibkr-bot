"""
src/ml/learner.py — SelfLearner: RandomForest meta-labeler + HRP optimiser.
Retrains weekly on live trade history. Activates after MIN_TRADES closed trades.
"""
from __future__ import annotations
import json
from datetime import date, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
import joblib
from loguru import logger
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from src.config import settings, cfg
from src.ml.features import FEATURE_NAMES, N_FEATURES
from src.ml.hrp import HRPOptimiser

class SelfLearner:
    MIN_TRADES           = settings.ML_MIN_TRADES
    LOOKBACK_DAYS        = settings.ML_LOOKBACK_DAYS
    MODEL_DIR            = settings.MODELS_DIR
    CONFIDENCE_THRESHOLD = cfg.get("meta_labeler",{}).get("confidence_threshold",0.55)

    def __init__(self):
        self.MODEL_DIR.mkdir(parents=True, exist_ok=True)
        self._model: Pipeline | None  = None
        self._model_path: Path | None = None
        self._hrp = HRPOptimiser(lookback_days=self.LOOKBACK_DAYS)
        self._last_retrain = None
        self._last_accuracy = 0.0
        self._last_n_trades = 0
        self._try_load_latest()

    def retrain(self, lookback_days=None) -> dict:
        lookback = lookback_days or self.LOOKBACK_DAYS
        logger.info(f"SelfLearner: retraining | lookback={lookback}d")
        from src.data.store import DataStore
        store = DataStore()
        since = (date.today() - timedelta(days=lookback)).isoformat()
        trades_df = store.load_trades(closed_only=True, start=since)
        if trades_df.empty or len(trades_df) < self.MIN_TRADES:
            msg = f"Only {len(trades_df)} trades (need {self.MIN_TRADES})"
            logger.info(f"SelfLearner: {msg} — skipping")
            return {"status":"skipped","reason":msg,"n_trades":len(trades_df)}
        trades = trades_df.to_dict("records")
        X, y = self._build_training_data(trades, store)
        if len(X) < self.MIN_TRADES:
            return {"status":"skipped","reason":f"only {len(X)} usable rows","n_trades":len(X)}
        model, accuracy, importances = self._train_model(X, y)
        model_path = self._save_model(model, accuracy, len(X), importances)
        store.register_model(str(model_path), accuracy, len(X),
                             f"lookback={lookback}d")
        self._model = model
        self._model_path = model_path
        self._last_retrain = date.today().isoformat()
        self._last_accuracy = accuracy
        self._last_n_trades = len(X)
        logger.success(f"Retrain done | accuracy={accuracy:.1%} | n={len(X)} | {model_path.name}")
        self._log_importances(importances)
        return {"status":"success","accuracy":round(accuracy,4),"n_trades":len(X),
                "model_path":str(model_path),"importances":importances}

    def predict(self, features: np.ndarray) -> tuple[int, float]:
        if self._model is None:
            return 1, 0.5
        try:
            prob = self._model.predict_proba([features])[0][1]
            return (1 if prob >= self.CONFIDENCE_THRESHOLD else 0), float(prob)
        except Exception as e:
            logger.warning(f"predict error: {e}")
            return 1, 0.5

    def get_hrp_weights(self, tickers=None, loader=None) -> dict:
        from src.config import TICKERS
        from src.data.loader import DataLoader
        tickers = tickers or TICKERS
        loader  = loader  or DataLoader(tickers=tickers)
        return self._hrp.compute_from_loader(loader, tickers)

    def get_position_sizes(self, tickers=None, loader=None, capital=None) -> dict:
        weights = self.get_hrp_weights(tickers=tickers, loader=loader)
        cap     = capital or settings.TOTAL_CAPITAL
        max_pos = cfg.get("risk",{}).get("max_position_usd", 2500.0)
        return self._hrp.position_sizes(weights, cap, max_pos)

    @property
    def is_active(self): return self._model is not None

    def status(self):
        return {"is_active":self.is_active,"model_path":str(self._model_path),
                "last_retrain":self._last_retrain,"accuracy":self._last_accuracy,
                "n_trades":self._last_n_trades,"min_trades":self.MIN_TRADES}

    def _build_training_data(self, trades, store):
        from src.data.loader import DataLoader
        tickers = list({t["ticker"] for t in trades})
        loader  = DataLoader(tickers=tickers, use_cache=True)
        X_rows, y_rows = [], []
        for i, trade in enumerate(trades):
            if trade.get("pnl") is None: continue
            df = loader.get(trade["ticker"])
            if df.empty: continue
            try:
                target = pd.Timestamp(trade.get("entry_time",""))
                if target.tzinfo is None: target = target.tz_localize("UTC")
                idx = df.index.get_indexer([target], method="nearest")[0]
                bar = df.iloc[idx]
            except Exception: continue
            X_rows.append(self._features_from_trade(trade, bar, trades[:i]))
            y_rows.append(1 if trade["pnl"] > 0 else 0)
        if not X_rows:
            return np.array([]).reshape(0, N_FEATURES), np.array([])
        return np.array(X_rows, dtype=np.float32), np.array(y_rows, dtype=np.int32)

    def _features_from_trade(self, trade, bar, past_trades):
        close = float(bar.get("close", 1.0)) or 1.0
        ema20 = float(bar.get("ema_slow", close)) or close
        recent = [t for t in past_trades[-10:] if t.get("pnl") is not None]
        wins   = [t["pnl"] for t in recent if t["pnl"] > 0]
        wr  = len(wins)/len(recent) if recent else 0.44
        exp = sum(t["pnl"] for t in recent)/len(recent) if recent else 0.0
        return np.array([
            float(trade.get("signal_score", 0.70)),
            float(bar.get("rsi_2",    10.0)),
            float(bar.get("bb_pct",    0.05)),
            float(bar.get("ema_diff",  0.0)) / close,
            float(bar.get("vol_ratio", 1.0)),
            float(bar.get("atr_pct",   0.01)),
            float(bar.get("bb_width",  0.02)),
            1.5, float(bar.get("atr",2.0))/close,
            close/ema20, close/ema20,
            wr, exp/close,
            1.0 if trade.get("side")=="LONG" else 0.0,
        ], dtype=np.float32)

    def _train_model(self, X, y):
        ml_cfg = cfg.get("meta_labeler",{})
        pipeline = Pipeline([
            ("scaler", StandardScaler()),
            ("rf", RandomForestClassifier(
                n_estimators=ml_cfg.get("n_estimators",200),
                max_depth=ml_cfg.get("max_depth",4),
                min_samples_leaf=5, class_weight="balanced",
                random_state=42, n_jobs=-1,
            )),
        ])
        tscv = TimeSeriesSplit(n_splits=5, gap=5)
        scores = cross_val_score(pipeline, X, y, cv=tscv, scoring="accuracy")
        accuracy = float(scores.mean())
        logger.info(f"CV accuracy: {accuracy:.1%} ±{scores.std():.1%}")
        pipeline.fit(X, y)
        importances = dict(zip(FEATURE_NAMES,
                               pipeline.named_steps["rf"].feature_importances_))
        return pipeline, accuracy, importances

    def _save_model(self, model, accuracy, n_trades, importances):
        today = date.today().isoformat()
        path  = self.MODEL_DIR / f"meta_labeler_{today}.pkl"
        meta  = self.MODEL_DIR / f"meta_labeler_{today}_meta.json"
        joblib.dump(model, path)
        meta.write_text(json.dumps({
            "date":today,"accuracy":accuracy,"n_trades":n_trades,
            "features":FEATURE_NAMES,
            "importances":{k:round(v,4) for k,v in importances.items()},
        }, indent=2), encoding="utf-8")
        return path

    def _try_load_latest(self):
        try:
            from src.data.store import DataStore
            path = DataStore().latest_model_path()
            if path and Path(path).exists():
                self._model = joblib.load(path)
                self._model_path = Path(path)
                logger.info(f"SelfLearner: loaded {path}")
            else:
                logger.info(f"SelfLearner: no model yet — needs {self.MIN_TRADES} trades")
        except Exception as e:
            logger.debug(f"SelfLearner load error: {e}")

    def _log_importances(self, importances):
        top = sorted(importances.items(), key=lambda x: x[1], reverse=True)[:5]
        for name, imp in top:
            logger.info(f"  {name}: {imp:.3f}")
