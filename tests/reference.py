"""An independent pass finder for cross-checking orbtrack.engine.passes.

It shares only the raw SGP4 model with the engine. The Earth rotation, observer
geometry and event search are written from scratch here instead of using Skyfield:
GMST from the IAU 1982 formula, WGS84 observer position, local-vertical elevation,
and a brute-force scan at 1 s steps with linear interpolation at the crossings.
"""

import numpy as np
from sgp4 import omm
from sgp4.api import Satrec, jday

A_KM = 6378.137
F = 1 / 298.257223563
E2 = F * (2 - F)


def satrec_from_omm(record: dict) -> Satrec:
    sat = Satrec()
    omm.initialize(sat, record)
    return sat


def gmst_rad(jd_ut1: np.ndarray) -> np.ndarray:
    """Greenwich mean sidereal time, IAU 1982 (Vallado eq. 3-47)."""
    t = (jd_ut1 - 2451545.0) / 36525.0
    seconds = 67310.54841 + (876600 * 3600 + 8640184.812866) * t + 0.093104 * t**2 - 6.2e-6 * t**3
    return np.radians((seconds % 86400) / 240.0)


def observer_ecef(lat_deg: float, lon_deg: float, height_km: float) -> tuple[np.ndarray, np.ndarray]:
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    n = A_KM / np.sqrt(1 - E2 * np.sin(lat) ** 2)
    position = np.array([
        (n + height_km) * np.cos(lat) * np.cos(lon),
        (n + height_km) * np.cos(lat) * np.sin(lon),
        (n * (1 - E2) + height_km) * np.sin(lat),
    ])
    up = np.array([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
    return position, up


def brute_force_passes(record, lat_deg, lon_deg, height_km, start_utc, hours, min_elevation_deg):
    """[(rise_jd, set_jd, max_elevation_deg)] for complete passes, times as UTC Julian dates."""
    sat = satrec_from_omm(record)
    jd0, fr0 = jday(*start_utc)
    offsets = np.arange(0, hours * 3600 + 1) / 86400.0
    jd = np.full_like(offsets, jd0)
    fr = fr0 + offsets
    err, r, _ = sat.sgp4_array(jd, fr)
    assert not err.any()

    theta = gmst_rad(jd + fr)  # UT1 taken as UTC; |UT1-UTC| < 0.9 s
    x = np.cos(theta) * r[:, 0] + np.sin(theta) * r[:, 1]
    y = -np.sin(theta) * r[:, 0] + np.cos(theta) * r[:, 1]
    sat_ecef = np.column_stack([x, y, r[:, 2]])

    obs, up = observer_ecef(lat_deg, lon_deg, height_km)
    rho = sat_ecef - obs
    elevation = np.degrees(np.arcsin(rho @ up / np.linalg.norm(rho, axis=1)))

    above = elevation >= min_elevation_deg
    edges = np.flatnonzero(np.diff(above.astype(int)))
    t = jd + fr

    def crossing(i):
        e0, e1 = elevation[i] - min_elevation_deg, elevation[i + 1] - min_elevation_deg
        return t[i] + (t[i + 1] - t[i]) * e0 / (e0 - e1)

    passes = []
    for i in edges:
        if not above[i]:  # rising edge
            later = edges[edges > i]
            if later.size:
                j = later[0]
                passes.append((crossing(i), crossing(j), float(elevation[i + 1 : j + 1].max())))
    return passes
