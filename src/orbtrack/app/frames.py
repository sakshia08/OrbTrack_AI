"""Data the browser needs to draw the globe, computed server-side with NumPy.

Positions are sent as compact Float32 keyframes in globe.gl's scene coordinates
(Earth radius = 100). The browser interpolates between two keyframes on the GPU,
so the server only has to propagate the catalog a couple of times a second.
"""

import base64

import numpy as np
from skyfield.api import Time, wgs84

from orbtrack.config import Observer
from orbtrack.engine import Catalog, Satellite, positions
from orbtrack.engine.passes import ephemeris, next_passes

GLOBE_RADIUS = 100.0
EARTH_RADIUS_KM = 6371.0

# Draw order = legend order. Indices are shared with globe.js.
ORBIT_CLASSES = ["LEO", "STARLINK", "MEO", "GEO", "HEO"]
CLASS_INDEX = {name: i for i, name in enumerate(ORBIT_CLASSES)}


def globe_xyz(lat_deg, lon_deg, alt_km) -> np.ndarray:
    """globe.gl's polar-to-cartesian mapping, vectorised. Returns float32 (..., 3)."""
    r = GLOBE_RADIUS * (1 + np.asarray(alt_km) / EARTH_RADIUS_KM)
    phi = np.radians(90 - np.asarray(lat_deg))
    theta = np.radians(90 - np.asarray(lon_deg))
    xyz = np.stack(
        [r * np.sin(phi) * np.cos(theta), r * np.cos(phi), r * np.sin(phi) * np.sin(theta)],
        axis=-1,
    )
    return np.nan_to_num(xyz, nan=0.0).astype(np.float32)  # failed propagations sit at Earth's centre


def b64(array: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(array).tobytes()).decode("ascii")


def catalog_payload(catalog: Catalog) -> dict:
    """Static per-satellite data, sent once per page: ids, names and orbit classes."""
    sats = list(catalog)
    return {
        "count": len(sats),
        "ids": b64(np.array([s.norad_id for s in sats], dtype=np.int32)),
        "classes": b64(np.array([CLASS_INDEX[s.orbit_class] for s in sats], dtype=np.uint8)),
        "names": "\n".join(s.name for s in sats),
    }


def keyframes(catalog: Catalog, t: Time, ahead_s: float) -> dict:
    """Catalog positions at ``t`` and ``t + ahead_s`` for the browser to interpolate between."""
    times = t.ts.tt_jd(t.tt + np.array([0.0, ahead_s]) / 86400)
    pos = positions(catalog, times)
    xyz = globe_xyz(pos.latitude, pos.longitude, pos.altitude_km)
    t_ms = times.utc_datetime()
    return {
        "a_ms": t_ms[0].timestamp() * 1000,
        "b_ms": t_ms[1].timestamp() * 1000,
        "a": b64(xyz[:, 0]),
        "b": b64(xyz[:, 1]),
    }


def subsolar_point(t: Time) -> tuple[float, float]:
    """Latitude and longitude where the Sun is overhead, for the day/night terminator."""
    eph = ephemeris()
    sun = eph["earth"].at(t).observe(eph["sun"]).apparent()
    lat, lon = wgs84.latlon_of(sun)
    return float(lat.degrees), float(lon.degrees)


def period_minutes(satellite: Satellite) -> float:
    return 2 * np.pi / satellite.sat.model.no_kozai  # no_kozai is radians per minute


def trail(satellite: Satellite, t: Time) -> dict:
    """The comet trail behind a satellite and its path for one orbit ahead, as [lat, lng, alt]."""
    period = period_minutes(satellite)
    past_min = min(0.25 * period, 40)
    step_min = max(period / 240, 0.25)
    offsets = np.arange(-past_min, period + step_min, step_min)
    offsets = np.union1d(offsets, [0.0])  # the trail must end exactly at the satellite
    times = t.ts.tt_jd(t.tt + offsets / 1440)
    geo = satellite.sat.at(times)
    lat, lon = wgs84.latlon_of(geo)
    alt = wgs84.height_of(geo).km / EARTH_RADIUS_KM
    points = np.column_stack([lat.degrees, lon.degrees, alt]).round(4)
    now = int(np.flatnonzero(offsets == 0.0)[0])
    return {"past": points[: now + 1].tolist(), "future": points[now:].tolist()}


def telemetry(satellite: Satellite, t: Time) -> dict:
    """What the HUD shows for the followed satellite."""
    geo = satellite.sat.at(t)
    lat, lon = wgs84.latlon_of(geo)
    return {
        "lat": float(lat.degrees),
        "lon": float(lon.degrees),
        "alt_km": float(wgs84.height_of(geo).km),
        "speed_km_s": float(np.linalg.norm(geo.velocity.km_per_s)),
        "period_min": float(period_minutes(satellite)),
        "tle_age_h": float((t - satellite.sat.epoch) * 24),
    }


def next_pass_text(satellite: Satellite, observer: Observer, t: Time) -> str:
    """One line about the next pass over the observer, e.g. "in 2 h 14 m · max 41° · visible"."""
    passes = next_passes(satellite, observer, t, hours=48, min_elevation_deg=10)
    if not passes:
        return "none in the next 48 h"
    p = passes[0]
    minutes = int((p.rise - t.utc_datetime()).total_seconds() // 60)
    when = f"in {minutes // 60} h {minutes % 60:02d} m" if minutes >= 60 else f"in {minutes} m"
    return f"{when} · max {p.max_elevation_deg:.0f}°" + (" · visible" if p.visible else "")
