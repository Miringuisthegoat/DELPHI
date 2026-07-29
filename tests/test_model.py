"""Tests for `app.ml.model.RandomForestPointsPredictor`."""

from __future__ import annotations

import numpy as np

from app.ml.features import FEATURE_NAMES
from app.ml.model import RandomForestPointsPredictor


def _synthetic_dataset(n=100, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, len(FEATURE_NAMES)))
    # Target loosely depends on the first feature so the model has
    # something real to learn (and a non-trivial feature importance).
    y = np.clip(5 + 2 * X[:, 0] + rng.normal(scale=0.5, size=n), 0, None)
    return X, y


def test_fit_predict_roundtrip():
    X, y = _synthetic_dataset()
    model = RandomForestPointsPredictor(n_estimators=20, random_state=0)
    model.fit(X, y)

    preds = model.predict(X[:5])
    assert preds.shape == (5,)
    assert (preds >= 0).all()


def test_predict_before_fit_raises():
    model = RandomForestPointsPredictor()
    try:
        model.predict(np.zeros((1, len(FEATURE_NAMES))))
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_feature_importances_sum_to_roughly_one():
    X, y = _synthetic_dataset()
    model = RandomForestPointsPredictor(n_estimators=20, random_state=0)
    model.fit(X, y)

    importances = model.feature_importances()
    assert set(importances.keys()) == set(FEATURE_NAMES)
    assert abs(sum(importances.values()) - 1.0) < 1e-6


def test_save_and_load_roundtrip(tmp_path):
    X, y = _synthetic_dataset()
    model = RandomForestPointsPredictor(n_estimators=20, random_state=0, version="9.9.9")
    model.fit(X, y)
    artifact_path = model.save(tmp_path)

    assert artifact_path.exists()

    reloaded = RandomForestPointsPredictor(version="9.9.9")
    reloaded.load(artifact_path)

    np.testing.assert_allclose(reloaded.predict(X[:3]), model.predict(X[:3]))


def test_latest_artifact_path_picks_most_recent(tmp_path):
    X, y = _synthetic_dataset()

    old = RandomForestPointsPredictor(n_estimators=10, random_state=0, version="1.0.0")
    old.fit(X, y)
    old.save(tmp_path)

    new = RandomForestPointsPredictor(n_estimators=10, random_state=0, version="2.0.0")
    new.fit(X, y)
    new_path = new.save(tmp_path)

    latest = RandomForestPointsPredictor.latest_artifact_path(tmp_path)
    assert latest == new_path
