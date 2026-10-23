from datetime import UTC, datetime, timedelta

from orbtrack.engine import next_passes
from tests.conftest import BANGALORE
from tests.reference import brute_force_passes

GATE_S = 60


def _jd_to_datetime(jd: float) -> datetime:
    return datetime(2000, 1, 1, 12, tzinfo=UTC) + timedelta(days=jd - 2451545.0)


def test_iss_passes_match_independent_reference(iss, stations_records, start):
    """Phase 1 gate: every ISS rise and set within 60 s of an independent calculation."""
    record = next(r for r in stations_records if r["NORAD_CAT_ID"] == 25544)
    reference = brute_force_passes(
        record,
        BANGALORE.latitude,
        BANGALORE.longitude,
        BANGALORE.elevation_m / 1000,
        (2026, 10, 23, 0, 0, 0),
        hours=72,
        min_elevation_deg=10,
    )
    ours = next_passes(iss, BANGALORE, start, hours=72, min_elevation_deg=10, with_visibility=False)

    assert len(ours) == len(reference) >= 5
    for p, (rise_jd, set_jd, max_el) in zip(ours, reference, strict=True):
        assert abs((p.rise - _jd_to_datetime(rise_jd)).total_seconds()) < GATE_S
        assert abs((p.set - _jd_to_datetime(set_jd)).total_seconds()) < GATE_S
        assert abs(p.max_elevation_deg - max_el) < 0.5


def test_daytime_passes_are_not_visible(iss, start):
    passes = next_passes(iss, BANGALORE, start, hours=72)
    assert all(isinstance(p.visible, bool) for p in passes)
    for p in passes:
        local_solar_hour = (p.culminate.hour + p.culminate.minute / 60 + BANGALORE.longitude / 15) % 24
        if 9 <= local_solar_hour <= 15:
            assert not p.visible
