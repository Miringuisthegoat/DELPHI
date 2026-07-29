"""
Phase 5: the heuristic predictor - DELPHI's cold-start fallback.

A Random Forest is only as good as the history it's trained on. At the
start of a season (or immediately after promotion/relegation reshuffles
the player pool) there may be zero or only a handful of
`PlayerGameweekStats` rows in the database - nowhere near enough to fit a
reliable model. Rather than refuse to make recommendations, or train a
model that's confidently wrong, `HeuristicPredictor` produces a
transparent, rules-based estimate from season-level context that's
already known before a ball is kicked: position, price, fixture
difficulty, team strength, and playing-time risk.

This is also the module that keeps every prediction explainable: each
call returns not just a number but a plain-English `reasoning` string,
satisfying the project's "never output a bare number" requirement even
before enough data exists for feature importances to do that job.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ml.features import FeatureVector
from app.models.enums import Position

# Rough position-level expected-points-per-90-minutes priors, calibrated
# against typical FPL scoring patterns (defenders/keepers score more from
# clean sheets than raw price alone suggests; forwards' price premium
# converts more directly into attacking returns).
_BASELINE_PPG: dict[Position, float] = {
    Position.GKP: 3.0,
    Position.DEF: 3.3,
    Position.MID: 3.6,
    Position.FWD: 3.8,
}

# Typical price (in millions) for a "average, rotation-safe" player in
# each position, used to normalise price into a quality multiplier.
_REFERENCE_PRICE: dict[Position, float] = {
    Position.GKP: 4.5,
    Position.DEF: 4.8,
    Position.MID: 6.5,
    Position.FWD: 6.8,
}

_FIXTURE_SWING_PER_DIFFICULTY_STEP = 0.45
_HOME_ADVANTAGE = 0.2
_NEUTRAL_DIFFICULTY = 3.0


@dataclass
class HeuristicPrediction:
    """A single-gameweek heuristic estimate plus its reasoning."""

    predicted_points: float
    confidence: float
    reasoning: str


class HeuristicPredictor:
    """Rules-based points estimator requiring no trained model.

    Stateless: every method is a pure function of the `FeatureVector` it's
    given, so it can be called standalone or as `DelphiPredictionEngine`'s
    fallback with no setup cost.
    """

    def predict(self, vector: FeatureVector) -> HeuristicPrediction:
        """Estimate points for one player/gameweek `FeatureVector`."""
        baseline = _BASELINE_PPG[vector.position]
        reference_price = _REFERENCE_PRICE[vector.position]

        # Price is the single best pre-season quality signal available -
        # the market has already priced in reputation, expected role, and
        # historic output. Square-root dampens the effect so a doubly
        # expensive player isn't predicted to score exactly double.
        price_factor = (max(vector.price_millions, 1.0) / reference_price) ** 0.5

        # If we do have some history (early season), blend in observed
        # recent form/points rather than relying on price alone.
        if vector.has_history:
            history_estimate = (
                0.5 * vector.form_weighted
                + 0.3 * vector.points_avg_3
                + 0.2 * vector.points_avg_season
            )
            baseline_estimate = baseline * price_factor
            # More history -> trust it more; a single gameweek is noisy.
            history_weight = min(vector.gameweeks_of_history / 5.0, 0.8)
            points = (
                history_weight * history_estimate
                + (1 - history_weight) * baseline_estimate
            )
        else:
            points = baseline * price_factor

        # Blank gameweek: no fixture at all.
        if vector.num_fixtures_this_gw <= 0:
            return HeuristicPrediction(
                predicted_points=0.0,
                confidence=0.95,
                reasoning=(
                    f"{vector.position.value} has no fixture this gameweek "
                    "(blank gameweek) - projected points are 0."
                ),
            )

        fixture_adjustment = (
            (_NEUTRAL_DIFFICULTY - vector.fixture_difficulty)
            * _FIXTURE_SWING_PER_DIFFICULTY_STEP
        )
        home_adjustment = _HOME_ADVANTAGE * vector.is_home

        points = points + fixture_adjustment + home_adjustment
        points = points * vector.expected_minutes_probability

        # Double gameweek: two fixtures roughly double the opportunity,
        # scaled down slightly since rotation risk is real across two games.
        if vector.num_fixtures_this_gw > 1:
            points = points * (vector.num_fixtures_this_gw * 0.9)

        points = max(points, 0.0)

        confidence = self._confidence(vector)
        reasoning = self._reasoning(
            vector, price_factor, fixture_adjustment, home_adjustment
        )

        return HeuristicPrediction(
            predicted_points=round(points, 2),
            confidence=confidence,
            reasoning=reasoning,
        )

    @staticmethod
    def _confidence(vector: FeatureVector) -> float:
        """Heuristic confidence: capped well below a trained model's ceiling.

        Confidence rises modestly with more accumulated history and falls
        for players whose playing time is genuinely uncertain, but never
        exceeds 0.6 - this predictor is explicitly a fallback, and callers
        (reports, dashboard) should be able to tell heuristic estimates
        apart from trained-model ones by confidence alone.
        """
        base = 0.35 + min(vector.gameweeks_of_history / 10.0, 0.2)
        base *= 0.5 + 0.5 * vector.expected_minutes_probability
        return round(min(base, 0.6), 2)

    @staticmethod
    def _reasoning(
        vector: FeatureVector,
        price_factor: float,
        fixture_adjustment: float,
        home_adjustment: float,
    ) -> str:
        parts: list[str] = []

        if vector.has_history:
            parts.append(
                f"recent form of {vector.form_weighted:.1f} pts/gw "
                f"over {vector.gameweeks_of_history} recorded gameweek(s)"
            )
        else:
            tier = "above" if price_factor > 1 else "at/below"
            parts.append(
                f"no gameweek history yet, so price (£{vector.price_millions:.1f}m, "
                f"{tier} the position's typical price) is used as the main quality signal"
            )

        if vector.fixture_difficulty < _NEUTRAL_DIFFICULTY:
            parts.append(
                f"a favourable fixture (difficulty {vector.fixture_difficulty:.0f}/5)"
            )
        elif vector.fixture_difficulty > _NEUTRAL_DIFFICULTY:
            parts.append(
                f"a tough fixture (difficulty {vector.fixture_difficulty:.0f}/5)"
            )

        if vector.is_home >= 0.5:
            parts.append("playing at home")

        if vector.expected_minutes_probability < 0.9:
            parts.append(
                f"only a {vector.expected_minutes_probability:.0%} chance of "
                "playing, which caps the projection"
            )

        if vector.num_fixtures_this_gw > 1:
            parts.append("a double gameweek, boosting the opportunity")

        return "Estimated from " + "; ".join(parts) + "."
