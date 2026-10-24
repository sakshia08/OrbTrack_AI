"""The maneuver detector: gradient-boosted trees over per-step features, graded on simulated fleets.

Features are robust z-scores against each satellite's own history, so one model covers the
ISS, sun-synchronous satellites and geostationary ones alike. Steps the model flags next
to each other are one event (a burn caught across two fits).
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

from orbtrack.detect.features import pair_features
from orbtrack.detect.synthetic import SimulatedSatellite, fleet

log = logging.getLogger("orbtrack")

VERSION = 1
EARTH_RADIUS_KM = 6378.137
REENTRY_PERIGEE_KM = 200  # below this, a satellite has days to weeks left
TARGET_PRECISION = 0.75  # the threshold is the best balance (F1) that keeps precision above this
RULE_Z = 6
TRAIN = {"n": 450, "days": 120, "seed": 1}
TUNE = {"n": 150, "days": 120, "seed": 2}
TEST = {"n": 300, "days": 120, "seed": 3}


@dataclass
class Event:
    norad_id: int
    name: str | None
    before_epoch: datetime  # last element set before the change
    epoch: datetime  # first element set after it
    kind: str  # "orbit raise", "orbit lower", "plane change" or "decay"
    delta_a_km: float
    delta_i_deg: float
    confidence: float
    reason: str


def _when(epoch: np.datetime64) -> str:
    return epoch.astype("datetime64[us]").item().strftime("%d %b %H:%M")


def _events_from_scores(p: np.ndarray, threshold: float) -> list[int]:
    """Indices of the strongest step in each run of flagged steps."""
    picks, run = [], []
    for k in np.flatnonzero(p >= threshold):
        if run and k - run[-1] > 1:
            picks.append(max(run, key=lambda j: p[j]))
            run = []
        run.append(int(k))
    if run:
        picks.append(max(run, key=lambda j: p[j]))
    return picks


@dataclass
class Detector:
    model: HistGradientBoostingClassifier
    threshold: float
    version: int = VERSION
    trained_on: str = ""

    def scores(self, series: dict[str, np.ndarray]) -> np.ndarray:
        X, _ = pair_features(series)
        return self.model.predict_proba(X)[:, 1] if len(X) else np.empty(0)

    def picks(self, series: dict[str, np.ndarray]) -> list[int]:
        return _events_from_scores(self.scores(series), self.threshold)

    def detect(self, series: dict[str, np.ndarray], norad_id: int = 0, name: str | None = None) -> list[Event]:
        X, info = pair_features(series)
        events = []
        if len(X):
            p = self.model.predict_proba(X)[:, 1]
            epochs = series["epoch"]
            for k in _events_from_scores(p, self.threshold):
                da, di = float(info["delta_a_km"][k]), float(info["delta_i_deg"][k])
                za = abs(da) / info["scale_a_km"][k]
                zi = abs(di) / info["scale_i_deg"][k]
                span = f"between {_when(epochs[k])} and {_when(epochs[k + 1])} UTC"
                if zi > za:
                    kind = "plane change"
                    reason = f"inclination moved {di:+.3f}° {span}, {zi:.0f}× its usual scatter"
                else:
                    kind = "orbit raise" if da > 0 else "orbit lower"
                    reason = (f"mean altitude {'rose' if da > 0 else 'fell'} {abs(da):.2f} km {span},"
                              f" {za:.0f}× its usual scatter")
                events.append(Event(norad_id, name, epochs[k].item(), epochs[k + 1].item(), kind,
                                    da, di, float(p[k]), reason))
        if len(series["epoch"]) and "eccentricity" in series:
            perigee = series["semimajor_km"][-1] * (1 - series["eccentricity"][-1]) - EARTH_RADIUS_KM
            if perigee < REENTRY_PERIGEE_KM:
                epoch = series["epoch"][-1].item()
                events.append(Event(norad_id, name, epoch, epoch, "decay", 0.0, 0.0, 0.9,
                                    f"perigee down to {perigee:.0f} km: re-entry likely within weeks"))
        return events


# --- Training and grading ---------------------------------------------------------


def _labelled(sats: list[SimulatedSatellite]) -> tuple[np.ndarray, np.ndarray]:
    Xs, ys = [], []
    for sat in sats:
        X, _ = pair_features(sat.series)
        y = np.zeros(len(X), dtype=int)
        for b in sat.burns:
            y[b.pair] = 1
            if b.partial and b.pair + 1 < len(y):
                y[b.pair + 1] = 1
        Xs.append(X)
        ys.append(y)
    return np.concatenate(Xs), np.concatenate(ys)


def _match(picks: list[int], sat: SimulatedSatellite) -> tuple[int, int, int]:
    """(true positives, false positives, missed burns); a pick within a step of a burn counts."""
    unmatched = list(sat.burns)
    tp = fp = 0
    for k in picks:
        hit = next((b for b in unmatched if b.pair - 1 <= k <= b.pair + 1 + b.partial), None)
        if hit:
            unmatched.remove(hit)
            tp += 1
        else:
            fp += 1
    return tp, fp, len(unmatched)


@dataclass
class Grade:
    precision: float
    recall: float
    burns: int
    flagged: int

    def __str__(self) -> str:
        return f"precision {self.precision:.0%}  recall {self.recall:.0%}  ({self.burns} burns, {self.flagged} flagged)"


def _grade(all_scores: list[np.ndarray], sats: list[SimulatedSatellite], threshold: float) -> Grade:
    tp = fp = fn = 0
    for p, sat in zip(all_scores, sats, strict=True):
        a, b, c = _match(_events_from_scores(p, threshold), sat)
        tp, fp, fn = tp + a, fp + b, fn + c
    return Grade(tp / max(tp + fp, 1), tp / max(tp + fn, 1), tp + fn, tp + fp)


def _score_all(model, sats):
    out = []
    for sat in sats:
        X, _ = pair_features(sat.series)
        out.append(model.predict_proba(X)[:, 1] if len(X) else np.empty(0))
    return out


def train(train_set: dict = TRAIN, tune_set: dict = TUNE) -> Detector:
    """Fit on one simulated fleet, then pick the alert threshold on a second one."""
    X, y = _labelled(fleet(**train_set))
    model = HistGradientBoostingClassifier(
        max_iter=250, learning_rate=0.08, max_leaf_nodes=31, l2_regularization=1.0, random_state=0
    ).fit(X, y)
    tune = fleet(**tune_set)
    scores = _score_all(model, tune)
    best = None
    for threshold in np.round(np.arange(0.1, 0.96, 0.025), 3):
        g = _grade(scores, tune, threshold)
        f1 = 2 * g.precision * g.recall / max(g.precision + g.recall, 1e-9)
        key = (g.precision >= TARGET_PRECISION, f1)
        if best is None or key > best[0]:
            best = (key, float(threshold))
    return Detector(model, best[1], trained_on=f"{train_set['n']} simulated satellites × {train_set['days']} days")


@dataclass
class Report:
    overall: Grade
    by_profile: dict[str, Grade]
    by_kind: dict[str, float]  # recall per burn kind
    threshold: float
    test_set: dict
    seconds: float
    baseline: Grade  # a plain rule: flag any step over RULE_Z times the usual scatter

    def text(self) -> str:
        lines = [
            (f"Test fleet: {self.test_set['n']} simulated satellites × {self.test_set['days']} days"
             f" (seed {self.test_set['seed']}, never seen in training), threshold {self.threshold:.3f}"),
            f"  overall    {self.overall}",
            *(f"  {name:10s} {g}" for name, g in self.by_profile.items()),
            "  recall by kind: " + ", ".join(f"{k} {v:.0%}" for k, v in self.by_kind.items()),
            f"  plain rule (any step over {RULE_Z}× the usual scatter): {self.baseline}",
            f"  graded in {self.seconds:.1f} s",
        ]
        return "\n".join(lines)


def evaluate(detector: Detector, test_set: dict = TEST) -> Report:
    started = time.perf_counter()
    sats = fleet(**test_set)
    scores = _score_all(detector.model, sats)
    by_profile = {}
    for name in sorted({s.profile for s in sats}):
        idx = [i for i, s in enumerate(sats) if s.profile == name]
        by_profile[name] = _grade([scores[i] for i in idx], [sats[i] for i in idx], detector.threshold)
    found: dict[str, list[int]] = {}
    for p, sat in zip(scores, sats, strict=True):
        picks = _events_from_scores(p, detector.threshold)
        for b in sat.burns:
            hit = any(b.pair - 1 <= k <= b.pair + 1 + b.partial for k in picks)
            found.setdefault(b.kind, []).append(hit)
    rule = [np.maximum(X[:, 0], X[:, 4]) / RULE_Z if len(X) else np.empty(0)
            for X, _ in (pair_features(s.series) for s in sats)]
    return Report(
        _grade(scores, sats, detector.threshold), by_profile,
        {k: float(np.mean(v)) for k, v in sorted(found.items())},
        detector.threshold, test_set, time.perf_counter() - started, _grade(rule, sats, 1.0),
    )


# --- Keeping a trained model ---------------------------------------------------------


def default_model_path() -> Path:
    from orbtrack.config import data_dir

    return data_dir() / "models" / f"detector-v{VERSION}.joblib"


def load_or_train(path: Path | None = None) -> Detector:
    """The saved detector, or a freshly trained one (about half a minute) saved for next time."""
    path = Path(path or default_model_path())
    if path.is_file():
        try:
            detector = joblib.load(path)
            if getattr(detector, "version", None) == VERSION:
                return detector
        except Exception as e:  # noqa: BLE001  an unreadable or outdated file: train again
            log.warning("OrbTrack: retraining the detector, could not load %s (%s)", path, e)
    detector = train()
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(detector, path)
    return detector


def detect_watchlist(store, detector: Detector, norad_ids: list[int] | None = None) -> list[Event]:
    """Run the detector over each watched satellite's whole history and store what it finds."""
    ids = norad_ids or store.watchlist()
    names = store.names(ids)
    found = []
    for n in ids:
        events = detector.detect(store.series(n), n, names.get(n))
        store.replace_events(n, events)
        found += events
    return found
