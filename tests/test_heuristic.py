"""Tests for `app.ml.heuristic.HeuristicPredictor`."""

from __future__ import annotations

from app.ml.features import FeatureVector
from app.ml.heuristic import HeuristicPredictor
from app.models.enums import Position


def _vector(**overrides) -> FeatureVector:
    defaults = dict(player_id=1, target_gameweek=5, position=Position.MID)
    defaults.update(overrides)
    return FeatureVector(**defaults)


def test_cold_start_prediction_is_positive_and_has_reasoning():
    vector = _vector(price_millions=8.0)
    result = HeuristicPredictor().predict(vector)

    assert result.predicted_points > 0
    assert result.confidence <= 0.6
    assert "price" in result.reasoning.lower() or "history" in result.reasoning.lower()


def test_higher_price_yields_higher_prediction():
    cheap = HeuristicPredictor().predict(_vector(price_millions=4.5))
    expensive = HeuristicPredictor().predict(_vector(price_millions=12.0))

    assert expensive.predicted_points > cheap.predicted_points


def test_easier_fixture_yields_higher_prediction():
    easy = HeuristicPredictor().predict(_vector(fixture_difficulty=1, price_millions=8.0))
    hard = HeuristicPredictor().predict(_vector(fixture_difficulty=5, price_millions=8.0))

    assert easy.predicted_points > hard.predicted_points


def test_blank_gameweek_predicts_zero():
    vector = _vector(num_fixtures_this_gw=0)
    result = HeuristicPredictor().predict(vector)

    assert result.predicted_points == 0.0
    assert "blank gameweek" in result.reasoning.lower()


def test_low_playing_time_probability_suppresses_prediction():
    nailed = HeuristicPredictor().predict(
        _vector(price_millions=8.0, expected_minutes_probability=1.0)
    )
    doubtful = HeuristicPredictor().predict(
        _vector(price_millions=8.0, expected_minutes_probability=0.25)
    )

    assert doubtful.predicted_points < nailed.predicted_points


def test_history_blends_in_when_available():
    vector = _vector(
        price_millions=5.0,
        gameweeks_of_history=5,
        form_weighted=10.0,
        points_avg_3=10.0,
        points_avg_season=10.0,
    )
    result = HeuristicPredictor().predict(vector)
    # A cheap player with excellent recent form should score above the
    # bare price-only baseline for a mid-priced midfielder.
    baseline_only = HeuristicPredictor().predict(_vector(price_millions=5.0))
    assert result.predicted_points > baseline_only.predicted_points
