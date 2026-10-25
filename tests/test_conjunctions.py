"""Close-approach screening, checked against brute force on orbits built to cross."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from orbtrack.data import socrates
from orbtrack.data.history import HistoryStore
from orbtrack.engine import Catalog
from orbtrack.engine import conjunctions as cj

START = datetime(2026, 10, 23, 0, tzinfo=UTC)
HOURS = 6


def iss_like(stations_records):
    return dict(next(r for r in stations_records if r["NORAD_CAT_ID"] == 25544))


def positions(sat, start, seconds):
    jd, fr = cj._jd(start)
    fr = fr + np.asarray(seconds) / 86400
    _, r, _ = sat.sat.model.sgp4_array(np.full(len(fr), jd), fr)
    return r


def brute_force(cat, a, b, start=START, hours=HOURS):
    """True closest approach of two satellites, by sampling every second and polishing to 1 ms."""
    t = np.arange(0, hours * 3600 + 1, 1.0)
    d = np.linalg.norm(positions(cat.get(b), start, t) - positions(cat.get(a), start, t), axis=1)
    k = int(np.argmin(d))
    fine = np.arange(t[max(k - 1, 0)], t[min(k + 1, len(t) - 1)], 0.001)
    df = np.linalg.norm(positions(cat.get(b), start, fine) - positions(cat.get(a), start, fine), axis=1)
    j = int(np.argmin(df))
    return start + timedelta(seconds=float(fine[j])), float(df[j])


def crossing(stations_records, raan_offset=25.0, target_km=2.0):
    """A copy of the ISS in a plane turned by ``raan_offset``, slid along its orbit until it passes
    about ``target_km`` from the ISS in the first two hours (a plain search, coarse then fine)."""
    iss = iss_like(stations_records)
    other = {**iss, "NORAD_CAT_ID": 99001, "OBJECT_NAME": "TEST DEBRIS", "OBJECT_ID": "2026-999A",
             "RA_OF_ASC_NODE": (iss["RA_OF_ASC_NODE"] + raan_offset) % 360}
    t = np.arange(0, 2 * 3600, 2.0)
    r_iss = positions(Catalog([iss]).get(25544), START, t)

    def closest(m):
        sat = Catalog([{**other, "MEAN_ANOMALY": m % 360}]).get(99001)
        return float(np.min(np.linalg.norm(positions(sat, START, t) - r_iss, axis=1)))

    best = min(np.arange(0, 360, 1.0), key=closest)
    for step in (0.1, 0.01, 0.001):
        best = min(best + np.arange(-10, 11) * step, key=lambda m: abs(closest(m) - target_km))
    other["MEAN_ANOMALY"] = float(best % 360)
    return [iss, other]


@pytest.fixture(scope="module")
def pair_records(stations_records):
    return crossing(stations_records)


@pytest.fixture(scope="module")
def pair(pair_records):
    return Catalog(pair_records)


def test_screening_matches_brute_force(pair):
    found = cj.screen([pair.get(25544)], pair, START, hours=HOURS, threshold_km=10)
    tca, miss = brute_force(pair, 25544, 99001)
    assert miss < 10  # the fixture really does come close
    best = min(found, key=lambda a: abs((a.tca - tca).total_seconds()))
    assert abs((best.tca - tca).total_seconds()) < 0.05
    assert abs(best.miss_km - miss) < 0.01
    # Nodes 25° apart at 51.6° inclination: the orbits cross at about 20°, so 2 × 7.66 × sin(10°).
    assert 2.2 < best.speed_km_s < 3.0
    assert best.secondary_name == "TEST DEBRIS"


def test_every_close_pass_is_found(pair):
    """Same phasing repeats each orbit, so there are several close passes; none may be missed."""
    found = cj.screen([pair.get(25544)], pair, START, hours=HOURS, threshold_km=60)
    t = np.arange(0, HOURS * 3600 + 1, 1.0)
    d = np.linalg.norm(positions(pair.get(99001), START, t) - positions(pair.get(25544), START, t), axis=1)
    minima = [i for i in range(1, len(d) - 1) if d[i] <= d[i - 1] and d[i] < d[i + 1] and d[i] < 59]
    assert len(minima) >= 3
    assert len(found) == len(minima)
    for i in minima:
        assert any(abs((a.tca - START).total_seconds() - i) < 1.5 for a in found)


def test_chunk_edges_do_not_lose_or_double_approaches(pair, monkeypatch):
    whole = cj.screen([pair.get(25544)], pair, START, hours=HOURS, threshold_km=60)
    monkeypatch.setattr(cj, "CHUNK_STEPS", 7)
    chunked = cj.screen([pair.get(25544)], pair, START, hours=HOURS, threshold_km=60)
    assert [round(a.miss_km, 3) for a in chunked] == [round(a.miss_km, 3) for a in whole]


def test_far_and_docked_objects_are_not_reported(stations_records):
    iss = iss_like(stations_records)
    docked = {**iss, "NORAD_CAT_ID": 99002, "OBJECT_NAME": "DOCKED", "MEAN_ANOMALY": iss["MEAN_ANOMALY"] + 0.001}
    high = {**iss, "NORAD_CAT_ID": 99003, "OBJECT_NAME": "HIGH", "MEAN_MOTION": 14.2}  # ~170 km higher
    cat = Catalog([iss, docked, high])
    assert cj.screen([cat.get(25544)], cat, START, hours=HOURS, threshold_km=10) == []


def test_altitude_filter_and_steps(stations_records, catalog):
    iss = catalog.get(25544)
    lo, hi = cj.band_km(iss)
    assert 6780 < lo < hi < 6810
    assert cj.step_for(iss) == 60
    geo = Catalog([{**iss_like(stations_records), "NORAD_CAT_ID": 99004, "MEAN_MOTION": 1.0027}]).get(99004)
    assert cj.step_for(geo) == 300


def test_a_pair_of_primaries_is_reported_once(pair):
    found = cj.screen([pair.get(25544), pair.get(99001)], pair, START, hours=HOURS, threshold_km=10)
    keys = [(a.tca.replace(microsecond=0), min(a.primary, a.secondary)) for a in found]
    assert len(keys) == len(set(keys)) and found


def test_element_set_age_is_measured_at_tca(pair):
    a = cj.screen([pair.get(25544)], pair, START, hours=HOURS, threshold_km=10)[0]
    epoch = datetime(2026, 10, 22, 23, 36, 4, tzinfo=UTC)
    assert abs(a.primary_age_h - (a.tca - epoch).total_seconds() / 3600) < 0.01


# --- Storage ---------------------------------------------------------------------------


def test_a_new_screening_replaces_future_approaches_only(tmp_path, pair):
    store = HistoryStore(tmp_path / "h.duckdb")
    found = cj.screen([pair.get(25544)], pair, START, hours=HOURS, threshold_km=60)
    store.replace_approaches(START, found)
    assert len(store.approaches()) == len(found)
    assert store.approaches()[0]["miss_km"] == pytest.approx(found[0].miss_km)
    later = START + timedelta(hours=3)  # a rescreen three hours on keeps what is already past
    store.replace_approaches(later, [])
    kept = store.approaches()
    assert kept and all(a["tca"] < later.replace(tzinfo=None) for a in kept)
    assert store.approaches(norad_id=99001) == kept
    assert store.approaches(since=later) == []


# --- SOCRATES -------------------------------------------------------------------------

SOCRATES_ROWS = """
<tr align=center> <td> <a class=button onclick="openWindowData('data.php?CATNR=25544,99001')">GP Data</a></td>
<td>25544</td> <td>ISS (ZARYA) [+]</td> <td>0.240</td> <td rowspan=2>{tca}</td> <td>{rng}</td> <td >{speed}</td> </tr>
<tr align=center> <td> <a class=button>50 km</a></td> <td>99001</td> <td>TEST DEBRIS [-]</td> <td>0.240</td>
<td>1.0E-03</td> <td>0.1</td> </tr>
"""


def test_socrates_table_and_element_parsing(pair_records):
    html = "<table>" + SOCRATES_ROWS.format(tca="2026-10-23 05:26:28.825", rng="1.244", speed="2.598") + "</table>"
    [c] = socrates.parse_table(html)
    assert (c.first, c.second, c.first_name, c.second_name) == (25544, 99001, "ISS (ZARYA)", "TEST DEBRIS")
    assert c.tca == datetime(2026, 10, 23, 5, 26, 28, 825000, tzinfo=UTC) and c.range_km == 1.244
    keys = list(pair_records[0])
    csv_text = ",".join(keys) + "\n" + "\n".join(",".join(str(r[k]) for k in keys) for r in pair_records)
    parsed = socrates.parse_elements(f"<html><pre>{csv_text}</pre></html>")
    assert [int(r["NORAD_CAT_ID"]) for r in parsed] == [25544, 99001]


def test_recomputing_a_socrates_conjunction(pair, pair_records):
    tca, miss = brute_force(pair, 25544, 99001)
    c = socrates.Conjunction(25544, 99001, "ISS", "TEST DEBRIS", tca, round(miss, 3), 2.6)
    check = socrates.recompute(c, pair_records)
    assert check.matched and abs(check.tca_diff_s) < 0.01 and abs(check.range_diff_m) < 1
    off = socrates.Conjunction(25544, 99001, "ISS", "TEST DEBRIS", tca, miss + 0.2, 2.6)
    assert not socrates.recompute(off, pair_records).matched  # 200 m out is a miss
    gone = socrates.Conjunction(25544, 12345, "ISS", "NOT THERE", tca, 1.0, 2.6)
    assert socrates.recompute(gone, pair_records).tca_diff_s is None

