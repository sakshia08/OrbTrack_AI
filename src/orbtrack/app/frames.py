"""Data the browser needs to draw the globe, computed server-side with NumPy.

Satellites are sent as compact Float32 keyframes of *inertial* (TEME) positions in
globe.gl's axes (Earth radius = 100). The browser slerps between two keyframes on the
GPU and turns the whole swarm by the sidereal angle, so orbits stay round even when
the time machine runs at 1000x and the keyframes are minutes apart.

globe.gl's axes, in Earth-fixed terms: x points to 90°E, y to the North Pole, z to 0°E.
"""

import base64
from datetime import datetime

import numpy as np
from skyfield.api import Time, wgs84
from skyfield.framelib import itrs

from orbtrack.config import Observer
from orbtrack.engine import Catalog, Pass, Satellite, gmst_radians, teme_positions, teme_track
from orbtrack.engine.passes import ephemeris, next_passes

GLOBE_RADIUS = 100.0
EARTH_RADIUS_KM = 6371.0

# Draw order = legend order. Indices are shared with globe.js.
ORBIT_CLASSES = ["LEO", "STARLINK", "MEO", "GEO", "HEO"]
CLASS_INDEX = {name: i for i, name in enumerate(ORBIT_CLASSES)}


def to_globe_axes(xyz_km) -> np.ndarray:
    """Earth-fixed or TEME (X, Y, Z) km to globe.gl scene units, which order the axes (Y, Z, X)."""
    xyz = np.asarray(xyz_km, dtype=float)
    return xyz[..., [1, 2, 0]] * (GLOBE_RADIUS / EARTH_RADIUS_KM)


def teme_to_ecef_globe(p, theta: float) -> np.ndarray:
    """Rotate TEME globe-axes points into Earth-fixed ones: what globe.js does with rotation.y = -theta."""
    p = np.asarray(p, dtype=float)
    c, s = np.cos(theta), np.sin(theta)
    x, y, z = p[..., 0], p[..., 1], p[..., 2]
    return np.stack([c * x - s * z, y, s * x + c * z], axis=-1)


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


def keyframes(catalog: Catalog, t: Time, span_s: float) -> dict:
    """Inertial catalog positions at ``t`` and ``t + span_s`` for the browser to slerp between.

    Also carries the sidereal angle at ``t`` (to turn TEME into Earth-fixed) and the Sun's
    direction in TEME, which lights the Earth and puts satellites in its shadow.
    """
    times = t.ts.tt_jd(t.tt + np.array([0.0, span_s]) / 86400)
    r, ok = teme_positions(catalog, times)
    xyz = np.where(ok[..., None], to_globe_axes(r), 0.0).astype(np.float32)  # failed ones sit at the centre
    t_ms = times.utc_datetime()
    return {
        "a_ms": t_ms[0].timestamp() * 1000,
        "b_ms": t_ms[1].timestamp() * 1000,
        "theta": float(gmst_radians(t)[0]),
        "sun": sun_direction_teme(t).round(6).tolist(),
        "a": b64(xyz[:, 0]),
        "b": b64(xyz[:, 1]),
    }


def sun_direction_teme(t: Time) -> np.ndarray:
    """Unit vector to the Sun in TEME globe axes, consistent with the GMST rotation used for satellites."""
    eph = ephemeris()
    ecef = to_globe_axes(eph["earth"].at(t).observe(eph["sun"]).apparent().frame_xyz(itrs).km)
    teme = teme_to_ecef_globe(ecef, -float(gmst_radians(t)[0]))
    return teme / np.linalg.norm(teme)


def period_minutes(satellite: Satellite) -> float:
    return 2 * np.pi / satellite.sat.model.no_kozai  # no_kozai is radians per minute


def trail(satellite: Satellite, t: Time) -> dict:
    """One full orbit in TEME globe axes, starting a little behind the satellite.

    Points are evenly spaced in time from ``t0_ms``, so the browser can fade the comet
    tail by "seconds since the satellite was here" and keep it glued to the satellite
    between updates.
    """
    period_s = period_minutes(satellite) * 60
    tail_s = min(0.25 * period_s, 40 * 60)
    count = 361
    offsets = np.linspace(-tail_s, period_s - tail_s, count)
    times = t.ts.tt_jd(t.tt + offsets / 86400)
    xyz = np.nan_to_num(to_globe_axes(teme_track(satellite, times))).round(3)
    return {
        "t0_ms": times[0].utc_datetime().timestamp() * 1000,
        "step_s": float(offsets[1] - offsets[0]),
        "period_s": float(period_s),
        "tail_s": float(tail_s),
        "points": xyz.tolist(),
    }


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


def next_pass(satellite: Satellite, observer: Observer, t: Time) -> Pass | None:
    """The next pass above 10° within 48 h, or one already in progress."""
    back = t.ts.tt_jd(t.tt - 20 / 1440)
    passes = next_passes(satellite, observer, back, hours=48, min_elevation_deg=10)
    now = t.utc_datetime()
    return next((p for p in passes if p.set > now), None)


def pass_text(p: Pass | None, now: datetime) -> str:
    """One line about a pass, e.g. "in 2 h 14 m · max 41° · visible" or "overhead now · max 41°"."""
    if p is None:
        return "none in the next 48 h"
    tail = f" · max {p.max_elevation_deg:.0f}°" + (" · visible" if p.visible else "")
    if p.rise <= now:
        return "overhead now" + tail
    minutes = int((p.rise - now).total_seconds() // 60)
    when = f"in {minutes // 60} h {minutes % 60:02d} m" if minutes >= 60 else f"in {minutes} m"
    return when + tail


# --- Your sky -----------------------------------------------------------------


def observer_frame(observer: Observer) -> dict:
    """Where the observer stands in globe coordinates, and their local east/north/up axes."""
    lat, lon = np.radians(observer.latitude), np.radians(observer.longitude)
    ecef = wgs84.latlon(observer.latitude, observer.longitude, observer.elevation_m).itrs_xyz.km
    axes = {
        "east": [-np.sin(lon), np.cos(lon), 0.0],
        "north": [-np.sin(lat) * np.cos(lon), -np.sin(lat) * np.sin(lon), np.cos(lat)],
        "up": [np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)],
    }
    frame = {name: np.asarray(v)[[1, 2, 0]].round(6).tolist() for name, v in axes.items()}
    frame["position"] = to_globe_axes(ecef).round(4).tolist()
    frame["lat"], frame["lon"] = observer.latitude, observer.longitude
    return frame


def sky_directions(alt_deg, az_deg, frame: dict) -> np.ndarray:
    """Unit vectors in globe coordinates for altitude/azimuth (azimuth from north through east)."""
    alt, az = np.radians(alt_deg), np.radians(az_deg)
    e, n, u = (np.asarray(frame[k]) for k in ("east", "north", "up"))
    horizontal = np.cos(alt)[..., None]
    return horizontal * (np.sin(az)[..., None] * e + np.cos(az)[..., None] * n) + np.sin(alt)[..., None] * u


def sky_passes(
    catalog: Catalog, norad_ids: list[int], observer: Observer, t: Time, hours: float = 24
) -> list[dict]:
    """Passes of a few satellites over the observer, each with its arc across the sky.

    Arcs run from horizon to horizon; only passes that climb above 10° are kept. A pass
    already in progress at ``t`` is included.
    """
    ts = t.ts
    back = ts.tt_jd(t.tt - 20 / 1440)
    topos = wgs84.latlon(observer.latitude, observer.longitude, observer.elevation_m)
    frame = observer_frame(observer)
    now = t.utc_datetime()
    out = []
    for norad_id in dict.fromkeys(norad_ids):
        sat = catalog.get(norad_id)
        if sat is None:
            continue
        for p in next_passes(sat, observer, back, hours=hours, min_elevation_deg=0):
            if p.set <= now or p.max_elevation_deg < 10:
                continue
            times = ts.linspace(ts.from_datetime(p.rise), ts.from_datetime(p.set), 64)
            alt, az, _ = (sat.sat - topos).at(times).altaz()
            path = sky_directions(np.maximum(alt.degrees, 0), az.degrees, frame)
            out.append(
                {
                    "norad_id": sat.norad_id,
                    "name": sat.name,
                    "pass": p,
                    "rise_ms": p.rise.timestamp() * 1000,
                    "culminate_ms": p.culminate.timestamp() * 1000,
                    "set_ms": p.set.timestamp() * 1000,
                    "max_el": round(p.max_elevation_deg, 1),
                    "rise_az": round(p.rise_azimuth_deg, 1),
                    "visible": bool(p.visible),
                    "path": path.round(4).tolist(),
                }
            )
    return sorted(out, key=lambda d: d["rise_ms"])


def sun_altitudes(observer: Observer, t0: Time, hours: float, step_min: float = 15) -> dict:
    """The Sun's altitude over the observer from ``t0`` every ``step_min``, for the night bands."""
    eph = ephemeris()
    topos = wgs84.latlon(observer.latitude, observer.longitude, observer.elevation_m)
    offsets = np.arange(0, hours * 60 + step_min / 2, step_min)
    times = t0.ts.tt_jd(t0.tt + offsets / 1440)
    alt = (eph["earth"] + topos).at(times).observe(eph["sun"]).apparent().altaz()[0].degrees
    return {
        "t0_ms": t0.utc_datetime().timestamp() * 1000,
        "step_ms": step_min * 60_000,
        "alt": np.atleast_1d(alt).round(1).tolist(),
    }


def above_horizon(catalog: Catalog, observer: Observer, t: Time) -> int:
    """How many satellites are above the observer's horizon at ``t``."""
    r, ok = teme_positions(catalog, t)
    theta = gmst_radians(t)[0]
    c, s = np.cos(theta), np.sin(theta)
    x, y, z = r[:, 0, 0], r[:, 0, 1], r[:, 0, 2]
    ecef = np.stack([c * x + s * y, -s * x + c * y, z], axis=-1)
    lat, lon = np.radians(observer.latitude), np.radians(observer.longitude)
    up = np.array([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
    site = wgs84.latlon(observer.latitude, observer.longitude, observer.elevation_m).itrs_xyz.km
    return int(np.count_nonzero(ok[:, 0] & ((ecef - site) @ up > 0)))
