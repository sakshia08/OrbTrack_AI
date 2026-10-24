"""Events on the page: rebuilt orbits, replays, the demo set, the 30-day strip and palette commands."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from orbtrack.app import events, palette
from orbtrack.data.history import HistoryStore
from orbtrack.detect.model import Event

WHEN = datetime(2026, 10, 22, 12, tzinfo=UTC)


@pytest.fixture
def iss_record(stations_records):
    return next(r for r in stations_records if r["NORAD_CAT_ID"] == 25544)


def radius_km(record, when):
    return float(np.linalg.norm(events._orbit(events.satrec(record), when, points=1)[0]))


# --- Orbits from stored element sets ----------------------------------------------------


def test_stored_element_sets_rebuild_the_same_orbit(tmp_path, stations_records, iss):
    store = HistoryStore(tmp_path / "h.duckdb")
    store.add(stations_records, "celestrak")
    epoch = datetime.fromisoformat(next(r for r in stations_records if r["NORAD_CAT_ID"] == 25544)["EPOCH"])
    record = store.element_sets(25544, [epoch])[epoch]
    when = WHEN.replace(hour=23)
    ours = events._orbit(events.satrec(record), when, points=1)[0]
    _, theirs, _ = iss.sat.model.sgp4(*events._jd(when))  # from the original CelesTrak record
    assert np.allclose(ours, theirs, atol=1e-3)


def test_where_gives_a_place_under_the_orbit(iss_record):
    lat, lon, height = events.where(iss_record, WHEN)
    assert abs(lat) <= 52 and -180 <= lon <= 180 and 380 < height < 460


# --- Demo burns and replays ------------------------------------------------------------


def test_a_demo_raise_starts_at_the_burn_and_peaks_opposite(iss_record):
    before, after = events.burned(iss_record, WHEN, delta_a_km=1.6, u_deg=70)
    assert abs(radius_km(after, WHEN) - radius_km(before, WHEN)) < 0.5  # a burn changes speed, not place
    half = WHEN + timedelta(minutes=46)  # about half an orbit later
    assert 2.6 < radius_km(after, half) - radius_km(before, half) < 3.6  # about 2 Δa on the far side
    lat, _, _ = events.where(before, WHEN)
    assert abs(lat - np.degrees(np.arcsin(np.sin(np.radians(70)) * np.sin(np.radians(51.6))))) < 1.5


def test_replay_draws_both_orbits_with_an_honest_scale(iss_record):
    before, after = events.burned(iss_record, WHEN, delta_a_km=1.6, u_deg=70)
    item = {"key": "k", "kind": "orbit raise", "burn_at": WHEN, "before": before, "after": after}
    r = events.replay(item)
    b, a = np.array(r["before"]), np.array(r["after"])
    gap = np.linalg.norm(a - b, axis=1)
    assert len(b) == len(a) == events.RING_POINTS + 1
    assert gap[0] < 0.05 * r["scale"] / 100 + 0.05  # the orbits meet at the burn
    assert abs(r["far"] - events.RING_POINTS // 2) < 20  # and part most half an orbit later
    assert r["scale"] in (50, 100, 200)  # a round exaggeration, shown to the user
    assert gap.max() <= events.TARGET_GAP * r["radius"] * 1.05
    assert 104 < r["radius"] < 108 and r["color"] == events.KIND_COLORS["orbit raise"]


def test_a_plane_change_tilts_the_orbit(iss_record):
    before, after = events.burned(iss_record, WHEN, delta_i_deg=0.03)
    assert after["INCLINATION"] == pytest.approx(before["INCLINATION"] + 0.03)
    lat, _, _ = events.where(before, WHEN)
    assert abs(lat) < 1.0  # done at the node
    r = events.replay({"key": "k", "kind": "plane change", "burn_at": WHEN, "before": before, "after": after})
    gap = np.linalg.norm(np.array(r["after"]) - np.array(r["before"]), axis=1)
    assert gap[0] < gap.max() / 10  # widest a quarter turn on, either way
    assert min(abs(r["far"] - k * events.RING_POINTS // 4) for k in (1, 3)) < 25


def test_demo_set_is_marked_everywhere(stations_records):
    items = events.demo(stations_records, WHEN)
    assert [i["norad_id"] for i in items] == [25544, 48274]  # only the demo satellites in this fixture
    for item in items:
        assert item["demo"] and item["reason"].startswith("DEMO")
        assert item["before_epoch"] < item["burn_at"] < item["epoch"]
    assert items[0]["burn_at"] == (WHEN - timedelta(days=1.3)).replace(microsecond=0)


# --- From the store -----------------------------------------------------------------------


def advance(record: dict, hours: float) -> dict:
    """The same orbit described at a later epoch, moved on with SGP4's own secular rates."""
    sat = events.satrec(record)
    minutes = hours * 60
    epoch = datetime.fromisoformat(record["EPOCH"]) + timedelta(hours=hours)
    return {
        **record,
        "EPOCH": epoch.strftime("%Y-%m-%dT%H:%M:%S.%f"),
        "MEAN_ANOMALY": (float(record["MEAN_ANOMALY"]) + np.degrees(sat.mdot * minutes)) % 360,
        "RA_OF_ASC_NODE": (float(record["RA_OF_ASC_NODE"]) + np.degrees(sat.nodedot * minutes)) % 360,
        "ARG_OF_PERICENTER": (float(record["ARG_OF_PERICENTER"]) + np.degrees(sat.argpdot * minutes)) % 360,
    }


def test_stored_events_come_back_ready_to_replay(tmp_path, iss_record):
    store = HistoryStore(tmp_path / "h.duckdb")
    burn = datetime.now(UTC).replace(microsecond=0) - timedelta(days=3)
    before, after = events.burned(iss_record, burn, delta_a_km=1.6, u_deg=40)
    after = advance(after, 6)  # the first element set after the burn comes out 6 h later
    store.add([before, after], "spacetrack")
    t0, t1 = burn.replace(tzinfo=None), burn.replace(tzinfo=None) + timedelta(hours=6)
    store.replace_events(25544, [Event(25544, "ISS (ZARYA)", t0, t1, "orbit raise", 1.6, 0.0, 0.9, "rose")])
    items = events.load(store)
    assert len(items) == 1
    item = items[0]
    assert item["burn_at"] == burn + timedelta(hours=3)
    assert not item["demo"] and -90 <= item["lat"] <= 90
    r = events.replay(item)
    assert r["scale"] >= 10 and len(r["after"]) == events.RING_POINTS + 1
    gap_km = np.linalg.norm(np.array(r["after"]) - np.array(r["before"]), axis=1) / r["scale"] * 6371 / 100
    assert 2.6 < gap_km.max() < 3.8 and gap_km.min() < 0.4  # 2 Δa opposite the burn, ~0 at it


def test_events_without_their_element_sets_are_skipped(tmp_path):
    store = HistoryStore(tmp_path / "h.duckdb")
    now = datetime.now(UTC).replace(tzinfo=None)
    store.replace_events(25544, [Event(25544, "ISS", now - timedelta(hours=6), now, "orbit raise", 1.0, 0, 0.9, "")])
    assert events.load(store) == []


# --- Text, strip and palette ---------------------------------------------------------------


def test_texts():
    assert events.size_text({"kind": "orbit lower", "delta_a_km": -0.9}) == "-0.90 km"
    assert events.size_text({"kind": "plane change", "delta_i_deg": 0.022}) == "+0.022°"
    assert events.ago_text(WHEN - timedelta(hours=5), WHEN) == "5 h"
    assert events.ago_text(WHEN - timedelta(days=4), WHEN) == "4 d"


def test_strip_marks_events_inside_the_window(stations_records):
    items = events.demo(stations_records, WHEN)
    old = {**items[0], "burn_at": WHEN - timedelta(days=45)}
    svg = events.strip_svg([*items, old], WHEN, 30)
    assert svg.startswith("<svg") and svg.count('r="2.6"') == 2  # the 45-day-old one is left out


def brief(stations_records, catalog):
    return [{"label": i["name"], "kind": i["kind"], "size": events.size_text(i), "ms": i["ms"], "key": i["key"],
             "norad_id": i["norad_id"], "demo": i["demo"]} for i in events.demo(stations_records, WHEN)]


def test_palette_lists_this_weeks_maneuvers(stations_records, catalog):
    found = palette.suggest("maneuvers this week", catalog, events=brief(stations_records, catalog),
                            now_s=WHEN.timestamp())
    assert found[0].action == "events"
    replays = [c for c in found if c.action == "replay"]
    assert len(replays) == 1 and "ISS" in replays[0].label  # Tiangong's (12.5 d ago) is older than a week
    assert "demo" in replays[0].hint


def test_palette_lists_one_satellites_events(stations_records, catalog):
    found = palette.suggest("events tiangong", catalog, events=brief(stations_records, catalog),
                            now_s=WHEN.timestamp())
    assert found[0].action == "replay" and found[0].arg.startswith("48274-")
    assert all(c.action != "follow" for c in found)  # not satellites named after the words
    assert palette.suggest("events iss", catalog, events=[], now_s=WHEN.timestamp())[0].action == "events"
