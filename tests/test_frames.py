import base64

import numpy as np
import pytest
from skyfield.api import wgs84

from orbtrack.app import frames


def decode(b64: str, dtype) -> np.ndarray:
    return np.frombuffer(base64.b64decode(b64), dtype=dtype)


@pytest.mark.parametrize(
    ("lat", "lng", "expected"),
    [(0, 0, (0, 0, 100)), (90, 0, (0, 100, 0)), (0, 90, (100, 0, 0)), (0, -90, (-100, 0, 0))],
)
def test_globe_xyz_matches_globe_gl(lat, lng, expected):
    assert np.allclose(frames.globe_xyz(lat, lng, 0.0), expected, atol=1e-4)


def test_globe_xyz_scales_with_altitude():
    assert np.linalg.norm(frames.globe_xyz(10, 20, frames.EARTH_RADIUS_KM)) == pytest.approx(200)


def test_failed_propagations_are_parked_at_the_centre():
    assert np.allclose(frames.globe_xyz(np.nan, np.nan, np.nan), 0)


def test_catalog_payload_round_trips(catalog):
    payload = frames.catalog_payload(catalog)
    ids = decode(payload["ids"], np.int32)
    classes = decode(payload["classes"], np.uint8)
    names = payload["names"].split("\n")
    assert payload["count"] == len(catalog) == len(ids) == len(classes) == len(names)
    i = list(ids).index(25544)
    assert names[i] == "ISS (ZARYA)"
    assert frames.ORBIT_CLASSES[classes[i]] == "LEO"


def test_keyframes_put_the_iss_where_skyfield_does(catalog, iss, start):
    frame = frames.keyframes(catalog, start, ahead_s=4.0)
    assert frame["b_ms"] - frame["a_ms"] == pytest.approx(4000, abs=1)
    a = decode(frame["a"], np.float32).reshape(-1, 3)
    b = decode(frame["b"], np.float32).reshape(-1, 3)
    i = [s.norad_id for s in catalog].index(25544)

    geo = iss.sat.at(start)
    lat, lon = wgs84.latlon_of(geo)
    expected = frames.globe_xyz(lat.degrees, lon.degrees, wgs84.height_of(geo).km)
    assert np.allclose(a[i], expected, atol=1e-3)
    # 4 s at 7.66 km/s is about 30.6 km, i.e. 0.48 globe units (plus a little Earth rotation)
    moved_km = np.linalg.norm(b[i] - a[i]) / frames.GLOBE_RADIUS * frames.EARTH_RADIUS_KM
    assert 29 < moved_km < 33


def test_trail_meets_the_satellite(iss, start):
    trail = frames.trail(iss, start)
    geo = iss.sat.at(start)
    lat, lon = wgs84.latlon_of(geo)
    assert trail["past"][-1][:2] == pytest.approx([lat.degrees, lon.degrees], abs=1e-3)
    assert trail["future"][0] == trail["past"][-1]
    assert len(trail["future"]) > len(trail["past"])


def test_subsolar_point_is_in_the_southern_tropics_in_late_october(ts):
    lat, lon = frames.subsolar_point(ts.utc(2026, 10, 23, 12))
    assert -13 < lat < -10  # declination of the Sun on 23 October is about -11.5°
    assert abs(lon) < 5  # noon UTC: the Sun is over the Greenwich meridian, give or take the equation of time


def test_telemetry_for_the_iss(iss, start):
    tm = frames.telemetry(iss, start)
    assert 380 < tm["alt_km"] < 450
    assert 92 < tm["period_min"] < 94
    assert tm["tle_age_h"] == pytest.approx(0.4, abs=0.1)
