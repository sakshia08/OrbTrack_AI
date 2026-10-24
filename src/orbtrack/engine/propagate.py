"""Bulk propagation: geodetic positions for many satellites at many times."""

from dataclasses import dataclass

import numpy as np
from skyfield.api import Time, wgs84
from skyfield.constants import DAY_S
from skyfield.sgp4lib import theta_GMST1982

from orbtrack.engine.catalog import Catalog, Satellite

WGS84_A_KM = 6378.137
WGS84_F = 1 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)


@dataclass
class Positions:
    """Arrays shaped (satellites, times). Entries that SGP4 could not propagate are NaN."""

    norad_ids: np.ndarray
    latitude: np.ndarray  # degrees
    longitude: np.ndarray  # degrees, -180..180
    altitude_km: np.ndarray
    speed_km_s: np.ndarray


def _utc_jd(t: Time) -> tuple[np.ndarray, np.ndarray]:
    # SGP4 epochs are UTC, so feed it UTC Julian dates exactly as Skyfield does.
    return np.atleast_1d(t.whole), np.atleast_1d(t.tai_fraction - t._leap_seconds() / DAY_S)


def ecef_to_geodetic(x, y, z):
    """WGS84 latitude/longitude (degrees) and height (km) from Earth-fixed km (Bowring)."""
    b = WGS84_A_KM * (1 - WGS84_F)
    ep2 = (WGS84_A_KM**2 - b**2) / b**2
    p = np.hypot(x, y)
    theta = np.arctan2(z * WGS84_A_KM, p * b)
    lat = np.arctan2(
        z + ep2 * b * np.sin(theta) ** 3,
        p - WGS84_E2 * WGS84_A_KM * np.cos(theta) ** 3,
    )
    n = WGS84_A_KM / np.sqrt(1 - WGS84_E2 * np.sin(lat) ** 2)
    height = p / np.cos(lat) - n
    return np.degrees(lat), np.degrees(np.arctan2(y, x)), height


def gmst_radians(t: Time) -> np.ndarray:
    """Greenwich mean sidereal angle: the rotation from TEME to Earth-fixed coordinates."""
    theta, _ = theta_GMST1982(np.atleast_1d(t.whole), np.atleast_1d(t.ut1_fraction))
    return theta


def teme_positions(catalog: Catalog, t: Time) -> tuple[np.ndarray, np.ndarray]:
    """Inertial (TEME) positions in km shaped (satellites, times, 3), and a mask of good ones."""
    jd, fr = _utc_jd(t)
    errors, r, _ = catalog.satrec_array.sgp4(jd, fr)
    return r, errors == 0


def teme_track(satellite: Satellite, t: Time) -> np.ndarray:
    """One satellite's TEME positions in km at many times, shaped (times, 3); NaN where SGP4 fails."""
    jd, fr = _utc_jd(t)
    errors, r, _ = satellite.sat.model.sgp4_array(jd, fr)
    r[errors != 0] = np.nan
    return r


def positions(catalog: Catalog, t: Time) -> Positions:
    """Propagate the whole catalog at once with sgp4's vectorised SatrecArray."""
    jd, fr = _utc_jd(t)
    errors, r, v = catalog.satrec_array.sgp4(jd, fr)

    # TEME -> Earth-fixed: rotate by Greenwich mean sidereal time (polar motion ignored, ~10 m).
    theta = gmst_radians(t)
    cos_t, sin_t = np.cos(theta), np.sin(theta)
    x = cos_t * r[..., 0] + sin_t * r[..., 1]
    y = -sin_t * r[..., 0] + cos_t * r[..., 1]
    lat, lon, alt = ecef_to_geodetic(x, y, r[..., 2])

    bad = errors != 0
    for arr in (lat, lon, alt):
        arr[bad] = np.nan
    speed = np.linalg.norm(v, axis=-1)
    speed[bad] = np.nan
    return Positions(
        norad_ids=np.array([s.norad_id for s in catalog]),
        latitude=lat,
        longitude=lon,
        altitude_km=alt,
        speed_km_s=speed,
    )


@dataclass
class GroundTrack:
    """One satellite's subpoints over time, split into segments at the antimeridian."""

    times: Time
    latitude: np.ndarray
    longitude: np.ndarray
    altitude_km: np.ndarray

    def segments(self) -> list[tuple[np.ndarray, np.ndarray]]:
        """(longitudes, latitudes) runs that never jump across ±180°, ready to plot as lines."""
        breaks = np.where(np.abs(np.diff(self.longitude)) > 180)[0] + 1
        return list(
            zip(np.split(self.longitude, breaks), np.split(self.latitude, breaks), strict=True)
        )


def ground_track(
    satellite: Satellite, start: Time, minutes: float = 90, step_s: float = 60
) -> GroundTrack:
    """Subpoints of one satellite from ``start`` for ``minutes``, every ``step_s`` seconds."""
    ts = start.ts
    offsets_days = np.arange(0, minutes * 60 + step_s / 2, step_s) / DAY_S
    times = ts.tt_jd(start.whole, start.tt_fraction + offsets_days)
    geocentric = satellite.sat.at(times)
    lat, lon = wgs84.latlon_of(geocentric)
    return GroundTrack(
        times=times,
        latitude=lat.degrees,
        longitude=lon.degrees,
        altitude_km=wgs84.height_of(geocentric).km,
    )
