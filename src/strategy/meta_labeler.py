"""
src/strategy/meta_labeler.py
─────────────────────────────
Meta-labeler — a secondary ML model that vets signals from the
primary signal engine before they reach the executor.

Concept (López de Prado, Chapter 3 — Advances in FM):
  - Primary model: the 3-layer signal engine fires a LONG/SHORT signal
  - Secondary model (meta-labeler): trained on PAST trade outcomes,
    it learns to predict "will THIS specific signal actually win?"
  - Only trades where BOTH agree get executed

This dramatically improves precision (win rate) at the cost of
slightly fewer trades — exactly the right trade-off for day trading.

Phase 3: stub that approves all signals (pass-through).
Phase 6: replaced with a trained RandomForest that improves weekly.
"""

from __future__ import annotations

import os
from pathlib import Path
from loguru import logger

from src.strategy.signal import Signal
from src.config import settings


class MetaLabeler:
    """
    Vets signals from the SignalEngine using a trained ML model.

    Phase 3 behaviour: always approves (model not yet trained).
    Phase 6 behaviour: loads a RandomForest and checks confidence.

    Usage:
        labeler = MetaLabeler()
        signal = labeler.approve(signal)
        if signal.meta_approved:
            # proceed with trade
    """

    # Minimum ML confidence to approve a signal (Phase 6)
    CONFIDENCE_THRESHOLD = 0.55

    def __init__(self, model_path: str | Path | None = None):
        self.model      = None
        self.model_path = model_path
        self._is_trained = False
        self._n_trades_seen = 0

        self._try_load_model()

    def approve(self, signal: Signal) -> Signal:
        """
        Vet a signal. Returns the signal with meta_approved set.

        If no model is trained yet → always approves (pass-through).
        If model is trained → approves only if confidence > threshold.
        """
        if not self._is_trained or self.model is None:
            # Phase 3: no model yet, let everything through
            signal.meta_approved = True
            return signal

        # Phase 6: run the ML model
        try:
            features = self._extract_features(signal)
            prob = self.model.predict_proba([features])[0][1]
            signal.meta_approved = prob >= self.CONFIDENCE_THRESHOLD

            if not signal.meta_approved:
                signal.blocked_reason = (
                    f"meta-labeler rejected (confidence={prob:.2f} "
                    f"< {self.CONFIDENCE_THRESHOLD})"
                )
                logger.debug(
                    f"MetaLabeler ❌ {signal.ticker} | confidence={prob:.2f}"
                )
            else:
                logger.debug(
                    f"MetaLabeler ✅ {signal.ticker} | confidence={prob:.2f}"
                )
        except Exception as e:
            logger.warning(f"MetaLabeler error: {e} — approving by default")
            signal.meta_approved = True

        return signal

    def approve_all(self, signals: list[Signal]) -> list[Signal]:
        """Vet a list of signals. Returns only approved ones."""
        approved = [self.approve(s) for s in signals]
        return [s for s in approved if s.meta_approved]

    @property
    def is_active(self) -> bool:
        """True if a trained model is loaded and vetting signals."""
        return self._is_trained and self.model is not None

    def status(self) -> dict:
        return {
            "is_active":       self.is_active,
            "model_path":      str(self.model_path) if self.model_path else None,
            "n_trades_seen":   self._n_trades_seen,
            "threshold":       self.CONFIDENCE_THRESHOLD,
            "phase":           6 if self.is_active else 3,
        }

    # ── Private ───────────────────────────────────────────────────────────────

    def _try_load_model(self) -> None:
        """Try to load the latest trained model from disk."""
        try:
            import joblib
            from src.data.store import DataStore

            # Check database for latest model path
            store = DataStore()
            model_path = self.model_path or store.latest_model_path()

            if model_path and Path(model_path).exists():
                self.model = joblib.load(model_path)
                self.model_path = model_path
                self._is_trained = True
                logger.info(f"MetaLabeler loaded model from {model_path}")
            else:
                logger.info(
                    "MetaLabeler: no trained model found — "
                    "running in pass-through mode (Phase 3). "
                    "Phase 6 will train the model automatically."
                )
        except Exception as e:
            logger.debug(f"MetaLabeler: could not load model ({e})")

    def _extract_features(self, signal: Signal) -> list[float]:
        """Extract ML features from a signal for model prediction."""
        return [
            signal.score,
            signal.rsi,
            signal.bb_pct,
            signal.ema_diff,
            signal.vol_ratio,
            signal.risk_reward,
            signal.atr,
            1.0 if signal.direction.value == "LONG" else -1.0,
        ]
