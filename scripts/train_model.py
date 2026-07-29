"""One-off / cron-friendly script to train the DELPHI Random Forest model.

Usage:
    python -m scripts.train_model
"""

from __future__ import annotations

from loguru import logger

from app.core.exceptions import PredictionError
from app.core.logging import configure_logging
from app.db.session import init_db, session_scope
from app.ml.training import ModelTrainingService


def main() -> None:
    configure_logging()
    init_db()

    trainer = ModelTrainingService()

    with session_scope() as db:
        try:
            result = trainer.train(db)
        except PredictionError as exc:
            logger.warning("Training skipped: {}", exc)
            raise SystemExit(1) from exc

    logger.info(
        "DELPHI trained: MAE={} RMSE={} R2={} -> saved to {}",
        result.metrics.mae,
        result.metrics.rmse,
        result.metrics.r2,
        result.model_path,
    )
    logger.info("Top features: {}", dict(list(result.feature_importances.items())[:5]))


if __name__ == "__main__":
    main()
