import numpy as np
from skyfield.api import wgs84

from orbtrack.engine import ground_track, positions


def test_bulk_positions_match_skyfield(catalog, ts):
    times = ts.utc(2026, 10, 23, 0, np.arange(0, 180, 15))
    pos = positions(catalog, times)
    assert pos.latitude.shape == (len(catalog), len(times))

    for row, sat in enumerate(catalog):
        geo = sat.sat.at(times)
        lat, lon = wgs84.latlon_of(geo)
        ok = np.isfinite(pos.latitude[row])
        assert np.allclose(pos.latitude[row][ok], lat.degrees[ok], atol=1e-3)
        dlon = (pos.longitude[row][ok] - lon.degrees[ok] + 180) % 360 - 180
        assert np.all(np.abs(dlon) < 1e-3)
        assert np.allclose(pos.altitude_km[row][ok], wgs84.height_of(geo).km[ok], atol=0.05)


def test_iss_altitude_and_speed_are_sane(catalog, start):
    pos = positions(catalog, start)
    row = list(pos.norad_ids).index(25544)
    assert 380 < pos.altitude_km[row, 0] < 450
    assert 7.5 < pos.speed_km_s[row, 0] < 7.8


def test_ground_track_segments_never_jump(iss, start):
    track = ground_track(iss, start, minutes=180, step_s=30)
    assert len(track.latitude) == 361
    assert np.abs(track.latitude).max() < 52  # ISS inclination is 51.6°
    segments = track.segments()
    assert len(segments) >= 2  # two orbits must cross the antimeridian
    for lons, _ in segments:
        assert np.abs(np.diff(lons)).max() < 180
