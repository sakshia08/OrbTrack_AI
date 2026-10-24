import base64
from datetime import timedelta

import numpy as np
import pytest
from skyfield.framelib import itrs

from orbtrack.app import frames
from orbtrack.engine import gmst_radians
from tests.conftest import BANGALORE


def decode(b64: str, dtype) -> np.ndarray:
    return np.frombuffer(base64.b64decode(b64), dtype=dtype)


def test_catalog_payload_round_trips(catalog):
    payload = frames.catalog_payload(catalog)
    ids = decode(payload["ids"], np.int32)
    classes = decode(payload["classes"], np.uint8)
    names = payload["names"].split("\n")
    assert payload["count"] == len(catalog) == len(ids) == len(classes) == len(names)
    i = list(ids).index(25544)
    assert names[i] == "ISS (ZARYA)"
    assert frames.ORBIT_CLASSES[classes[i]] == "LEO"


def angle_deg(u, v) -> float:
    u, v = np.asarray(u, dtype=float), np.asarray(v, dtype=float)
    return float(np.degrees(np.arccos(np.clip(u @ v / np.linalg.norm(u) / np.linalg.norm(v), -1, 1))))


def ecef_globe(sat, t):
    """Where Skyfield's Earth-fixed (ITRS) frame puts a satellite, in globe units."""
    return frames.to_globe_axes(sat.sat.at(t).frame_xyz(itrs).km)


def test_to_globe_axes_matches_globe_gl():
    # 0°E on the equator is +z, 90°E is +x, the North Pole is +y
    assert np.allclose(frames.to_globe_axes([frames.EARTH_RADIUS_KM, 0, 0]), (0, 0, 100))
    assert np.allclose(frames.to_globe_axes([0, frames.EARTH_RADIUS_KM, 0]), (100, 0, 0))
    assert np.allclose(frames.to_globe_axes([0, 0, frames.EARTH_RADIUS_KM]), (0, 100, 0))


def test_keyframes_turned_by_theta_put_the_iss_where_skyfield_does(catalog, iss, start):
    frame = frames.keyframes(catalog, start, span_s=4.0)
    assert frame["b_ms"] - frame["a_ms"] == pytest.approx(4000, abs=1)
    a = decode(frame["a"], np.float32).reshape(-1, 3)
    b = decode(frame["b"], np.float32).reshape(-1, 3)
    i = [s.norad_id for s in catalog].index(25544)

    # What globe.js draws: the inertial keyframe turned by the sidereal angle
    drawn = frames.teme_to_ecef_globe(a[i], frame["theta"])
    assert np.allclose(drawn, ecef_globe(iss, start), atol=0.02)  # 0.02 units = 1.3 km
    # 4 s at 7.66 km/s is about 30.6 km, i.e. 0.48 globe units
    moved_km = np.linalg.norm(b[i] - a[i]) / frames.GLOBE_RADIUS * frames.EARTH_RADIUS_KM
    assert 29 < moved_km < 32


def test_slerp_between_far_apart_keyframes_stays_on_the_orbit(catalog, iss, start, ts):
    # At 1000x the keyframes are ~15 min apart; the midpoint must not cut inside the orbit.
    frame = frames.keyframes(catalog, start, span_s=880.0)
    i = [s.norad_id for s in catalog].index(25544)
    a = decode(frame["a"], np.float32).reshape(-1, 3)[i].astype(float)
    b = decode(frame["b"], np.float32).reshape(-1, 3)[i].astype(float)
    ra, rb = np.linalg.norm(a), np.linalg.norm(b)
    w = np.arctan2(np.linalg.norm(np.cross(a / ra, b / rb)), np.dot(a / ra, b / rb))
    mid = (np.sin(w / 2) * a / ra + np.sin(w / 2) * b / rb) / np.sin(w)
    mid = mid / np.linalg.norm(mid) * (ra + rb) / 2
    t_mid = ts.tt_jd(start.tt + 440 / 86400)
    truth = frames.teme_to_ecef_globe(mid, gmst_radians(t_mid)[0])
    error_km = np.linalg.norm(truth - ecef_globe(iss, t_mid)) / frames.GLOBE_RADIUS * frames.EARTH_RADIUS_KM
    assert error_km < 25  # a straight line would be ~260 km inside the orbit


def test_sun_direction_lights_the_subsolar_point(start):
    sun = frames.teme_to_ecef_globe(frames.sun_direction_teme(start), gmst_radians(start)[0])
    lat = np.degrees(np.arcsin(sun[1]))
    assert -13 < lat < -10  # declination of the Sun on 23 October is about -11.5°
    lon = np.degrees(np.arctan2(sun[0], sun[2]))
    assert abs(abs(lon) - 180) < 5  # midnight UTC: the Sun is over the antimeridian


def test_trail_starts_behind_and_meets_the_satellite(catalog, iss, start):
    trail = frames.trail(iss, start)
    frame = frames.keyframes(catalog, start, span_s=4.0)
    i = [s.norad_id for s in catalog].index(25544)
    a = decode(frame["a"], np.float32).reshape(-1, 3)[i]
    points = np.array(trail["points"])
    k = round(trail["tail_s"] / trail["step_s"])  # the sample taken at `start`
    assert np.allclose(points[k], a, atol=0.01)
    assert trail["t0_ms"] == pytest.approx(frame["a_ms"] - trail["tail_s"] * 1000, abs=1)
    assert trail["step_s"] * (len(points) - 1) == pytest.approx(trail["period_s"])


def test_telemetry_for_the_iss(iss, start):
    tm = frames.telemetry(iss, start)
    assert 380 < tm["alt_km"] < 450
    assert 92 < tm["period_min"] < 94
    assert tm["tle_age_h"] == pytest.approx(0.4, abs=0.1)


def test_pass_text(iss, start):
    p = frames.next_pass(iss, BANGALORE, start)
    assert p is not None
    before = p.rise - timedelta(hours=2, minutes=14)
    assert frames.pass_text(p, before).startswith("in 2 h 14 m · max ")
    assert frames.pass_text(p, p.culminate).startswith("overhead now")
    assert frames.pass_text(None, before) == "none in the next 48 h"


# --- Your sky -------------------------------------------------------------------


def test_observer_frame_is_orthonormal_and_up_points_out():
    f = frames.observer_frame(BANGALORE)
    e, n, u = (np.array(f[k]) for k in ("east", "north", "up"))
    assert np.allclose([e @ n, n @ u, u @ e], 0, atol=1e-6)
    assert np.allclose(np.cross(e, n), u, atol=1e-6)  # east x north = up, a right-handed frame
    position = np.array(f["position"])
    assert angle_deg(u, position) < 0.2  # geodetic up vs geocentric direction
    assert np.linalg.norm(position) == pytest.approx(100.1, abs=0.05)


def test_sky_directions_point_where_alt_az_says(start):
    f = frames.observer_frame(BANGALORE)
    assert np.allclose(frames.sky_directions(90, 0, f), f["up"], atol=1e-9)
    assert np.allclose(frames.sky_directions(0, 0, f), f["north"], atol=1e-9)
    assert np.allclose(frames.sky_directions(0, 90, f), f["east"], atol=1e-9)


def test_sky_pass_arcs_point_at_the_iss(catalog, iss, start):
    passes = frames.sky_passes(catalog, [25544], BANGALORE, start)
    assert passes and all(p["max_el"] >= 10 for p in passes)
    assert [p["rise_ms"] for p in passes] == sorted(p["rise_ms"] for p in passes)
    p = passes[0]
    # The middle of the arc looks at where the ISS really is, from where the observer stands
    k, n = len(p["path"]) // 2, len(p["path"])
    t = start.ts.from_datetime(p["pass"].rise + (p["pass"].set - p["pass"].rise) * k / (n - 1))
    seen = ecef_globe(iss, t) - np.array(frames.observer_frame(BANGALORE)["position"])
    assert angle_deg(p["path"][k], seen) < 0.2


def test_above_horizon_counts_the_iss_during_its_pass(catalog, start):
    p = frames.next_pass(catalog.get(25544), BANGALORE, start)
    during = start.ts.from_datetime(p.culminate)
    after = start.ts.from_datetime(p.set + timedelta(minutes=30))
    others = frames.above_horizon(catalog, BANGALORE, after)
    assert 0 <= others < len(catalog)
    assert frames.above_horizon(catalog, BANGALORE, during) >= 1


def test_sun_altitudes_over_bangalore_swing_day_to_night(start):
    bands = frames.sun_altitudes(BANGALORE, start, hours=24)
    alt = np.array(bands["alt"])
    assert len(alt) == 97 and bands["step_ms"] == 15 * 60_000
    assert alt.max() > 50 and alt.min() < -50  # 13°N in October: high noon, deep night
