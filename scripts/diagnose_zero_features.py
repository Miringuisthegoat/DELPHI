"""One-off diagnostic: check whether rolling/form features are actually
populated in the training matrix, or stuck at defaults."""

from __future__ import annotations

import numpy as np
from app.core.logging import configure_logging
from app.db.session import init_db, session_scope
from app.ml.features import FEATURE_NAMES
from app.ml.training import ModelTrainingService


def main() -> None:
    configure_logging()
    init_db()

    with session_scope() as db:
        X, y = ModelTrainingService().build_training_data(db)

    print(f"Training matrix shape: {X.shape}")
    print(f"{'feature':<32} {'nonzero_rows':>12} {'pct_nonzero':>12} {'mean':>10}")
    for i, name in enumerate(FEATURE_NAMES):
        col = X[:, i]
        nonzero = int(np.count_nonzero(col))
        pct = 100 * nonzero / len(col) if len(col) else 0.0
        print(f"{name:<32} {nonzero:>12} {pct:>11.1f}% {col.mean():>10.3f}")

    suspects = [
        "points_avg_3", "points_avg_5", "points_avg_season", "form_weighted",
        "minutes_avg_3", "minutes_avg_5", "minutes_avg_season",
        "goals_avg_5", "assists_avg_5", "bonus_avg_5", "rotation_risk",
    ]
    print("\n--- Suspect features ---")
    for name in suspects:
        idx = FEATURE_NAMES.index(name)
        col = X[:, idx]
        print(f"{name}: all_zero={np.all(col == 0)}, nonzero_count={np.count_nonzero(col)}")


if __name__ == "__main__":
    main()