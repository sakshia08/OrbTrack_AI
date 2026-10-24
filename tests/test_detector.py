"""Maneuver detector: simulator, features, the trained model and the events it stores. All offline."""

from datetime import UTC, datetime

import numpy as np
import pytest

from orbtrack.data.history import HistoryStore
from orbtrack.detect import model, synthetic
from orbtrack.detect.features import MIN_POINTS, NAMES, pair_features
from tests.test_history import iss_history

SMALL_TRAIN = {"n": 160, "days": 90, "seed": 11}
SMALL_TUNE = {"n": 60, "days": 90, "seed": 12}


@pytest.fixture(scope="module")
def detector():
    return model.train(SMALL_TRAIN, SMALL_TUNE)


def quiet_series(n=200, step_at=None, size=1.5, spike_at=None, noise=0.03, seed=0):
    """ISS-like history: four sets a day, gentle decay, optional step (a burn) or spike (a bad fit)."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) * 0.25
    a = 6790 - 0.08 * t + noise * rng.standard_normal(n)
    if step_at is not None:
        a[step_at + 1 :] += size
    if spike_at is not None:
        a[spike_at] += 12 * noise
    return {
        "epoch": synthetic.EPOCH0 + (t * 86400e6).astype("timedelta64[us]"),
        "semimajor_km": a,
        "inclination": 51.64 + 3e-4 * rng.standard_normal(n),
        "eccentricity": np.full(n, 5e-4),
    }


# --- Simulator -------------------------------------------------------------------


def test_fleet_is_reproducible_and_labelled():
    one, two = synthetic.fleet(20, seed=5), synthetic.fleet(20, seed=5)
    assert [s.profile for s in one] == [s.profile for s in two]
    assert np.array_equal(one[0].series["semimajor_km"], two[0].series["semimajor_km"])
    burns = [b for s in one for b in s.burns]
    assert burns and {b.kind for b in burns} <= {"raise", "lower", "plane"}
    for s in one:
        assert np.all(np.diff(s.series["epoch"]) > np.timedelta64(0, "us"))
        assert all(0 <= b.pair < len(s.series["epoch"]) - 1 for b in s.burns)


def test_a_simulated_reboost_is_where_the_label_says():
    rng = np.random.default_rng(3)
    for _ in range(20):
        sat = synthetic.simulate(synthetic.PROFILES["station"], 60, rng)
        for b in sat.burns:
            a = sat.series["semimajor_km"]
            jump = np.median(a[b.pair + 2 : b.pair + 6]) - np.median(a[max(b.pair - 3, 0) : b.pair + 1])
            assert np.sign(jump) == np.sign(b.size) or abs(b.size) < 0.6


# --- Features --------------------------------------------------------------------


def test_features_shape_and_short_histories():
    X, info = pair_features(quiet_series(50))
    assert X.shape == (49, len(NAMES)) and len(info["delta_a_km"]) == 49
    X, info = pair_features(quiet_series(MIN_POINTS - 1))
    assert X.shape == (0, len(NAMES)) and info == {}


def test_a_step_persists_and_a_spike_snaps_back():
    X, info = pair_features(quiet_series(step_at=100))
    assert X[100, 0] > 20 and X[100, 1] > 20  # big step, and it stuck
    assert abs(info["delta_a_km"][100] - 1.5) < 0.1  # drift is taken out
    X, _ = pair_features(quiet_series(spike_at=100))
    assert X[99, 0] > 8  # the jump up into the bad fit is large...
    assert abs(X[99, 1]) < 3  # ...but the level did not change
    assert X[99, 2] < -5  # and the next step snaps back


def test_steady_decay_is_normal():
    X, info = pair_features(quiet_series(noise=0.0))
    assert np.all(X[:, 0] < 1)  # the drag trend is not an event
    assert abs(np.median(info["delta_a_km"])) < 0.01


# --- Model -----------------------------------------------------------------------


def test_detector_finds_a_reboost_and_ignores_a_bad_fit(detector):
    events = detector.detect(quiet_series(step_at=120, spike_at=60), 25544, "ISS (ZARYA)")
    assert [e.kind for e in events] == ["orbit raise"]
    e = events[0]
    assert e.before_epoch < e.epoch and 0.5 <= e.confidence <= 1
    assert abs(e.delta_a_km - 1.5) < 0.1 and "mean altitude rose 1." in e.reason


def test_detector_names_lowering_and_plane_changes(detector):
    lowered = detector.detect(quiet_series(step_at=90, size=-1.2))
    assert [e.kind for e in lowered] == ["orbit lower"]
    s = quiet_series()
    s["inclination"][131:] += 0.03
    assert [e.kind for e in detector.detect(s)] == ["plane change"]


def test_rare_false_alarms_on_quiet_history(detector):
    alarms = sum(len(detector.detect(quiet_series(n=400, seed=s))) for s in range(20))
    assert alarms <= 4  # 20 × 100 days of plain scatter: about one alarm a year at most


def test_low_perigee_is_a_decay(detector):
    s = quiet_series()
    s["semimajor_km"] = s["semimajor_km"] - 6790 + 6560  # perigee about 180 km
    kinds = [e.kind for e in detector.detect(s)]
    assert kinds[-1] == "decay"


def test_gate_on_an_unseen_fleet(detector):
    """The Phase 4b gate (recall ≥ 80% at precision ≥ 70%), here with a small, quick model."""
    report = model.evaluate(detector, {"n": 120, "days": 90, "seed": 13})
    assert report.overall.recall >= 0.8 and report.overall.precision >= 0.7
    assert report.overall.precision > report.baseline.precision  # better than a plain threshold
    assert "precision" in report.text() and set(report.by_profile) == {"geo", "leo", "station"}


def test_model_is_saved_and_reloaded(tmp_path, monkeypatch, detector):
    monkeypatch.setattr(model, "train", lambda *a, **k: detector)
    path = tmp_path / "models" / "detector.joblib"
    first = model.load_or_train(path)
    assert path.is_file()
    monkeypatch.setattr(model, "train", lambda *a, **k: pytest.fail("should load, not train"))
    assert model.load_or_train(path).threshold == first.threshold
    path.write_bytes(b"not a model")
    monkeypatch.setattr(model, "train", lambda *a, **k: detector)
    assert model.load_or_train(path).threshold == detector.threshold  # unreadable file: retrain


# --- Through the history store ---------------------------------------------------------


def test_watchlist_detection_stores_events(tmp_path, stations_records, detector):
    store = HistoryStore(tmp_path / "history.duckdb")
    store.add(iss_history(stations_records, days=40, start=datetime(2026, 9, 1), raise_on=25), "spacetrack")
    events = model.detect_watchlist(store, detector, [25544])
    assert [e.kind for e in events] == ["orbit raise"]
    stored = store.events(norad_id=25544)
    assert len(stored) == 1 and stored[0]["name"] == "ISS (ZARYA)"
    assert datetime(2026, 9, 25, 18) <= stored[0]["epoch"] <= datetime(2026, 9, 26, 6)
    assert store.events(since=datetime(2026, 10, 1, tzinfo=UTC)) == []
    model.detect_watchlist(store, detector, [25544])  # a rerun replaces, never duplicates
    assert len(store.events()) == 1
