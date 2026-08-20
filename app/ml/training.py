"""
Phase 5 (patched post-Phase-12/13): `ModelTrainingService` - turns
accumulated gameweek history (live AND historical) into a trained DELPHI
model.

This is deliberately separate from `DelphiPredictionEngine` (which
*uses* a model to produce weekly recommendations): training is a
periodic, relatively expensive batch job (run after each gameweek's
results are in, or on demand via `POST /predictions/train`), while
generating predictions is a fast, frequent read path.

PATCH NOTE (post Phase 12/13 diagnosis)
----------------------------------------
Originally, `build_training_data()` iterated only `PlayerGameweekStats`
(the live, current-season table) to build (X, y) pairs. Phase 12 added
~50k rows to a separate `HistoricalPlayerGameweekStats` table, which fed
*into* `PlayerFeatureBuilder`'s rolling window (once `features.py` was
patched) but was never itself a source of *labelled training examples* -
every historical gameweek's own `total_points` was completely invisible
to `train()`. This under-uses the whole point of Phase 12: DELPHI should
be learning from 5 seasons' worth of "given this player's state before
gameweek N, they scored X points in gameweek N" examples, not just
however many gameweeks have been played in the current live season.

`build_training_data()` below now walks both tables. For each row it
builds the same no-lookahead `FeatureVector` `PlayerFeatureBuilder`
always has, but the *target gameweek* passed in depends on which table
the label came from:
  - Live rows: build features as of that row's own `gameweek` (existing
    behaviour, unchanged).
  - Historical rows: build features as of that row's `(season, gameweek)`
    - `PlayerFeatureBuilder` doesn't currently understand "target season"
      as a concept (see the TODO below), so as an interim, defensible
      approximation, historical rows are trained using only *prior rows
      within the same historical table* as their lookback window (via a
      small season/gameweek-scoped variant), keeping the no-lookahead
      rule intact without requiring a deeper `PlayerFeatureBuilder`
      rewrite this pass.

TODO (flagged, not solved here): `PlayerFeatureBuilder.build()` assumes
"target_gameweek" is always current-season. Building a *fully* correct
historical feature vector (respecting each historical season's own fixture
difficulty/opponent strength) would mean extending `_apply_fixture_context`
to accept a `season` parameter and querying historical fixtures too. For
now, historical training rows use *neutral* fixture context (the
FeatureVector defaults: difficulty=3.0, is_home=0.0, team/opponent
strength=1100.0) rather than guessing - this trades a small amount of
signal for correctness (never claiming to know a historical fixture's
difficulty when we may not have that data queryable in the same way).
Revisit once `HistoricalDataFetcher`'s fixture-level data (if any) is
confirmed available.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from loguru import logger
from sklearn.model_selection import train_test_split
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import PredictionError
from app.ml.features import (
    FEATURE_NAMES,
    FeatureVector,
    PlayerFeatureBuilder,
    _HistoryRow,
    _avg,
    _safe,
    _weighted_form,
)
from app.ml.model import RandomForestPointsPredictor, TrainingMetrics
from app.models.player import Player
from app.models.player_stats import PlayerGameweekStats

try:
    from app.models.player_stats_historical import HistoricalPlayerGameweekStats
except ImportError:  # pragma: no cover - only hit on older checkouts
    HistoricalPlayerGameweekStats = None  # type: ignore[assignment, misc]


@dataclass
class TrainingResult:
    """Everything the API/CLI needs to report back after a training run."""

    metrics: TrainingMetrics
    model_path: str
    feature_importances: dict[str, float]


class ModelTrainingService:
    """Builds the training set from the database (live + historical) and
    fits `RandomForestPointsPredictor`."""

    def __init__(self, feature_builder: PlayerFeatureBuilder | None = None) -> None:
        self._feature_builder = feature_builder or PlayerFeatureBuilder()

    def build_training_data(self, db: Session) -> tuple[np.ndarray, np.ndarray]:
        """Build (X, y) from every historical (player, gameweek) result,
        across both the live and historical stats tables.

        For every live `PlayerGameweekStats` row (an actual, already-played
        current-season gameweek), the feature vector is reconstructed
        exactly as it would have looked *before* that gameweek was played
        (see `PlayerFeatureBuilder`'s no-lookahead rule), using the
        player's full combined live+historical prior record.

        For every `HistoricalPlayerGameweekStats` row (a past season's
        already-played gameweek), the same no-lookahead principle applies
        but scoped within the historical record only - see this module's
        docstring for why fixture-context fields stay neutral for these
        rows.

        Rows with zero prior history are skipped either way - with
        nothing to compute rolling features from, they'd all be
        default/neutral values and would just teach the model noise.
        """
        live_features, live_targets = self._build_from_live_rows(db)
        historical_features, historical_targets = self._build_from_historical_rows(db)

        features = live_features + historical_features
        targets = live_targets + historical_targets

        if not features:
            return np.empty((0, len(FEATURE_NAMES))), np.empty((0,))

        logger.info(
            "Training data assembled: {} live-season examples, {} historical "
            "examples ({} total)",
            len(live_features),
            len(historical_features),
            len(features),
        )

        return np.array(features, dtype=float), np.array(targets, dtype=float)

    def _build_from_live_rows(
        self, db: Session
    ) -> tuple[list[list[float]], list[float]]:
        """Existing behaviour: one training example per live-season row,
        using `PlayerFeatureBuilder.build()` (which now also pulls in
        historical rows for the rolling window - see `features.py`)."""
        rows = (
            db.execute(
                select(PlayerGameweekStats).order_by(
                    PlayerGameweekStats.player_id, PlayerGameweekStats.gameweek
                )
            )
            .scalars()
            .all()
        )

        features: list[list[float]] = []
        targets: list[float] = []
        player_cache: dict[int, Player | None] = {}

        for row in rows:
            player = player_cache.get(row.player_id)
            if player is None and row.player_id not in player_cache:
                player = db.get(Player, row.player_id)
                player_cache[row.player_id] = player
            if player is None:
                continue

            vector = self._feature_builder.build(db, player, row.gameweek)
            if not vector.has_history:
                continue

            features.append(vector.to_row())
            targets.append(float(row.total_points))

        return features, targets

    def _build_from_historical_rows(
        self, db: Session
    ) -> tuple[list[list[float]], list[float]]:
        """One training example per matched historical row, using a
        historical-only lookback window (see module docstring for why
        fixture context stays neutral here).

        Skipped entirely if `HistoricalPlayerGameweekStats` isn't
        available (older checkout), or if a historical row has no
        resolved `matched_player_id` (unmatched by the Phase 12 name
        matcher - excluded defensively, same as `features.py`).
        """
        if HistoricalPlayerGameweekStats is None:
            return [], []

        rows = (
            db.execute(
                select(HistoricalPlayerGameweekStats)
                .where(HistoricalPlayerGameweekStats.matched_player_id.is_not(None))
                .order_by(
                    HistoricalPlayerGameweekStats.matched_player_id,
                    HistoricalPlayerGameweekStats.season,
                    HistoricalPlayerGameweekStats.gameweek,
                )
            )
            .scalars()
            .all()
        )

        features: list[list[float]] = []
        targets: list[float] = []
        player_cache: dict[int, Player | None] = {}

        # Group rows per matched player so each player's prior-row lookback
        # only ever looks at *that player's* earlier (season, gameweek)
        # entries, never another player's.
        rows_by_player: dict[int, list] = {}
        for row in rows:
            rows_by_player.setdefault(row.matched_player_id, []).append(row)

        for player_id, player_rows in rows_by_player.items():
            player = player_cache.get(player_id)
            if player is None and player_id not in player_cache:
                player = db.get(Player, player_id)
                player_cache[player_id] = player
            if player is None:
                # Historical row matched to a player id that no longer
                # exists in the live players table (e.g. long since
                # relegated/retired and pruned) - skip, don't guess.
                continue

            # player_rows is already sorted (season, gameweek) ascending
            # from the query above.
            for i, row in enumerate(player_rows):
                prior_rows = player_rows[:i]
                if not prior_rows:
                    continue  # no history yet for this player -> skip, same rule as live rows

                vector = self._historical_vector(player, row, prior_rows)
                features.append(vector.to_row())
                targets.append(_safe(row.total_points))

        return features, targets

    @staticmethod
    def _historical_vector(
        player: Player, target_row, prior_rows: list
    ) -> FeatureVector:
        """Build a `FeatureVector` for one historical row, using only
        that player's earlier historical rows as the rolling window.

        Deliberately mirrors `PlayerFeatureBuilder.build()`'s rolling-
        average logic rather than calling it directly, since the DB-driven
        version expects a live `target_gameweek` int and queries live
        fixtures - neither of which applies to a past season's row. See
        this module's docstring TODO for the fixture-context tradeoff.
        """
        history = [
            _HistoryRow(
                minutes=_safe(r.minutes),
                total_points=_safe(r.total_points),
                goals_scored=_safe(r.goals_scored),
                assists=_safe(r.assists),
                clean_sheets=_safe(r.clean_sheets),
                goals_conceded=_safe(r.goals_conceded),
                bonus=_safe(r.bonus),
                bps=_safe(r.bps),
                ict_index=_safe(r.ict_index),
                influence=_safe(r.influence),
                creativity=_safe(r.creativity),
                threat=_safe(r.threat),
                cbi=_safe(getattr(r, "clearances_blocks_interceptions", None)),
                tackles=_safe(getattr(r, "tackles", None)),
                recoveries=_safe(getattr(r, "recoveries", None)),
                defensive_contribution=_safe(
                    getattr(r, "defensive_contribution", None)
                ),
            )
            for r in prior_rows
        ]

        vector = FeatureVector(
            player_id=player.id,
            # Historical rows don't map to a "real" current-season
            # gameweek; this value is never used for a DB lookback query
            # here (unlike PlayerFeatureBuilder.build), only stored for
            # bookkeeping/debugging.
            target_gameweek=_safe(target_row.gameweek, default=0.0).__int__()
            if hasattr(target_row, "gameweek")
            else 0,
            position=player.position,
            price_millions=player.price_millions,
            ownership_percent=player.ownership_percent,
            price_trend=player.price_trend,
            is_gkp=float(player.position.value == "GKP"),
            is_def=float(player.position.value == "DEF"),
            is_mid=float(player.position.value == "MID"),
            is_fwd=float(player.position.value == "FWD"),
            # Playing-time probability isn't knowable retroactively from a
            # past season in the same way as "current injury status" - use
            # a neutral 1.0 rather than applying the player's *current*
            # status to a past-season row, which would be a lookahead-style
            # leak in reverse (today's injury status has no bearing on
            # whether they played in 2022-23).
            expected_minutes_probability=1.0,
            gameweeks_of_history=len(history),
        )

        if history:
            last_3 = history[-3:]
            last_5 = history[-5:]

            minutes_all = [h.minutes for h in history]
            points_all = [h.total_points for h in history]

            vector.minutes_avg_3 = _avg([h.minutes for h in last_3])
            vector.minutes_avg_5 = _avg([h.minutes for h in last_5])
            vector.minutes_avg_season = _avg(minutes_all)

            vector.points_avg_3 = _avg([h.total_points for h in last_3])
            vector.points_avg_5 = _avg([h.total_points for h in last_5])
            vector.points_avg_season = _avg(points_all)
            vector.form_weighted = _weighted_form(points_all)

            vector.goals_avg_5 = _avg([h.goals_scored for h in last_5])
            vector.assists_avg_5 = _avg([h.assists for h in last_5])
            vector.clean_sheets_avg_5 = _avg([h.clean_sheets for h in last_5])
            vector.goals_conceded_avg_5 = _avg([h.goals_conceded for h in last_5])
            vector.bonus_avg_5 = _avg([h.bonus for h in last_5])
            vector.bps_avg_5 = _avg([h.bps for h in last_5])
            vector.ict_index_avg_5 = _avg([h.ict_index for h in last_5])
            vector.influence_avg_5 = _avg([h.influence for h in last_5])
            vector.creativity_avg_5 = _avg([h.creativity for h in last_5])
            vector.threat_avg_5 = _avg([h.threat for h in last_5])

            vector.cbi_avg_5 = _avg([h.cbi for h in last_5])
            vector.tackles_avg_5 = _avg([h.tackles for h in last_5])
            vector.recoveries_avg_5 = _avg([h.recoveries for h in last_5])
            vector.defensive_contribution_avg_5 = _avg(
                [h.defensive_contribution for h in last_5]
            )

            from statistics import pstdev

            vector.rotation_risk = (
                pstdev(minutes_all[-5:]) if len(minutes_all[-5:]) > 1 else 0.0
            )

        # Fixture context deliberately left at FeatureVector's neutral
        # defaults (difficulty=3.0, is_home=0.0, strengths=1100.0) - see
        # module docstring TODO.
        return vector

    def train(self, db: Session) -> TrainingResult:
        """Build the training set, fit the model, evaluate, and persist it.

        Raises:
            PredictionError: if fewer than
                `settings.ml_min_samples_for_training` labelled rows are
                available - training on too little data would silently
                produce a model less reliable than the heuristic
                predictor it's meant to replace.
        """
        X, y = self.build_training_data(db)

        if X.shape[0] < settings.ml_min_samples_for_training:
            raise PredictionError(
                f"Only {X.shape[0]} labelled training rows are available "
                f"(need at least {settings.ml_min_samples_for_training}). "
                "This is expected in preseason or the first few gameweeks "
                "of a season - DELPHI will keep using the heuristic "
                "predictor until enough gameweek history accumulates."
            )

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=settings.ml_random_state
        )

        model = RandomForestPointsPredictor(
            n_estimators=settings.ml_rf_n_estimators,
            max_depth=settings.ml_rf_max_depth,
            min_samples_leaf=settings.ml_rf_min_samples_leaf,
            random_state=settings.ml_random_state,
            version=settings.ml_model_version,
        )
        model.fit(X_train, y_train)

        predictions = model.predict(X_test)
        metrics = self._evaluate(y_test, predictions, len(X_train), len(X_test))

        artifact_path = model.save(settings.ml_model_dir)

        logger.info(
            "DELPHI training complete: MAE={:.2f} RMSE={:.2f} R2={:.2f} "
            "(train={}, test={})",
            metrics.mae,
            metrics.rmse,
            metrics.r2,
            metrics.n_train,
            metrics.n_test,
        )

        return TrainingResult(
            metrics=metrics,
            model_path=str(artifact_path),
            feature_importances=model.feature_importances(),
        )

    @staticmethod
    def _evaluate(
        y_true: np.ndarray, y_pred: np.ndarray, n_train: int, n_test: int
    ) -> TrainingMetrics:
        errors = y_pred - y_true
        mae = float(np.mean(np.abs(errors)))
        rmse = float(np.sqrt(np.mean(errors**2)))

        ss_res = float(np.sum(errors**2))
        ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2))
        r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

        return TrainingMetrics(
            mae=round(mae, 3),
            rmse=round(rmse, 3),
            r2=round(r2, 3),
            n_train=n_train,
            n_test=n_test,
            trained_at=datetime.now(timezone.utc),
        )