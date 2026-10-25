"""Close-approach screening: watchlist satellites against a catalog, over the next few days.

1. **Altitude filter.** Only objects whose perigee-apogee band overlaps the primary's
   (plus a margin) can come close, which leaves hundreds to a few thousand per primary.
2. **Coarse pass.** Everything is propagated together with vectorised SGP4 at a fixed
   step (60 s in low orbit, 5 min higher up). Each pair's distance curve is scanned for
   local minima that could hide a close approach between two samples: a minimum is kept
   while its sampled distance is under ``threshold + half a step of relative motion``.
3. **Refinement.** Each kept minimum is refined with Newton steps on Δr · Δv = 0 using
   SGP4 itself, which gives the time of closest approach (TCA) to well under a second.

Pairs that barely move relative to each other (docked vehicles, formation flyers) are
skipped: their "approaches" are permanent, not events.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
from sgp4.api import SatrecArray

from orbtrack.engine.catalog import EARTH_RADIUS_KM, MU_KM3_S2, Catalog, Satellite

ALTITUDE_MARGIN_KM = 30.0  # SGP4 short-period terms move a satellite tens of km around its mean band
MIN_RELATIVE_SPEED = 0.01  # km/s; slower than this is a docked or formation-flying pair
CHUNK_STEPS = 120  # time samples propagated at once (memory: satellites × steps × 6 doubles)


@dataclass
class Approach:
    primary: int
    secondary: int
    primary_name: str
    secondary_name: str
    tca: datetime  # time of closest approach, UTC
    miss_km: float
    speed_km_s: float  # relative speed at TCA
    primary_age_h: float  # how old each element set is at TCA: older means less certain
    secondary_age_h: float


def _jd(when: datetime) -> tuple[float, float]:
    days = (when - datetime(2000, 1, 1, 12, tzinfo=UTC)) / timedelta(days=1)
    whole = math.floor(days)
    return 2451545.0 + whole, days - whole


def band_km(sat: Satellite) -> tuple[float, float]:
    """Mean perigee and apogee radius (km from Earth's centre)."""
    m = sat.sat.model
    a = (MU_KM3_S2 / (m.no_kozai / 60) ** 2) ** (1 / 3)
    return a * (1 - m.ecco), a * (1 + m.ecco)


def step_for(sat: Satellite) -> float:
    """Coarse step: relative speeds reach 15 km/s in low orbit but only a few hundred m/s near GEO."""
    revs_per_day = sat.sat.model.no_kozai * 1440 / (2 * math.pi)
    return 60.0 if revs_per_day > 5 else 300.0


def _state(sat: Satellite, when: datetime) -> tuple[np.ndarray, np.ndarray]:
    jd, fr = _jd(when)
    error, r, v = sat.sat.model.sgp4(jd, fr)
    if error:
        return np.full(3, np.nan), np.full(3, np.nan)
    return np.array(r), np.array(v)


def _refine(p: Satellite, s: Satellite, when: datetime, max_shift_s: float) -> tuple[datetime, float, float]:
    """Newton steps on d/dt |Δr|² = 0, with the relative velocity as the derivative."""
    for _ in range(4):
        rp, vp = _state(p, when)
        rs, vs = _state(s, when)
        dr, dv = rs - rp, vs - vp
        dv2 = float(dv @ dv)
        if not np.isfinite(dv2) or dv2 == 0:
            break
        shift = float(np.clip(-(dr @ dv) / dv2, -max_shift_s, max_shift_s))
        when += timedelta(seconds=shift)
        if abs(shift) < 1e-3:
            break
    rp, vp = _state(p, when)
    rs, vs = _state(s, when)
    return when, float(np.linalg.norm(rs - rp)), float(np.linalg.norm(vs - vp))


def _candidates(primary: Satellite, others: list[Satellite], bands: np.ndarray, threshold_km: float) -> np.ndarray:
    lo, hi = band_km(primary)
    m = ALTITUDE_MARGIN_KM + threshold_km
    ok = (bands[:, 0] - m < hi) & (bands[:, 1] + m > lo)
    ok &= np.array([o.norad_id != primary.norad_id for o in others])
    return np.flatnonzero(ok)


def _screen_group(primaries: list[Satellite], others: list[Satellite], bands: np.ndarray, start: datetime,
                  hours: float, threshold_km: float, step_s: float) -> list[Approach]:
    cand = {p.norad_id: _candidates(p, others, bands, threshold_km) for p in primaries}
    rows = np.unique(np.concatenate([c for c in cand.values()] or [np.empty(0, int)])).astype(int)
    if not len(rows):
        return []
    index = {int(r): i for i, r in enumerate(rows)}  # catalog row -> row in the propagated block
    sats = [others[r] for r in rows]
    array = SatrecArray([s.sat.model for s in sats])
    prim_array = SatrecArray([p.sat.model for p in primaries])

    n_steps = int(hours * 3600 / step_s) + 1
    jd0, fr0 = _jd(start)
    found: list[Approach] = []
    prev = None  # last two samples of the previous chunk, so minima at chunk edges are not missed
    for c0 in range(0, n_steps, CHUNK_STEPS):
        k = np.arange(c0, min(c0 + CHUNK_STEPS, n_steps))
        fr = fr0 + k * step_s / 86400
        jd = np.full(len(k), jd0)
        err_s, r_s, v_s = array.sgp4(jd, fr)
        err_p, r_p, v_p = prim_array.sgp4(jd, fr)
        r_s[err_s != 0] = np.nan
        r_p[err_p != 0] = np.nan
        times = k
        if prev is not None:
            r_s = np.concatenate([prev[0], r_s], axis=1)
            v_s = np.concatenate([prev[1], v_s], axis=1)
            r_p = np.concatenate([prev[2], r_p], axis=1)
            v_p = np.concatenate([prev[3], v_p], axis=1)
            times = np.concatenate([prev[4], k])
        prev = (r_s[:, -2:], v_s[:, -2:], r_p[:, -2:], v_p[:, -2:], times[-2:])

        for pi, p in enumerate(primaries):
            ci = [index[int(r)] for r in cand[p.norad_id]]
            if not ci:
                continue
            dr = r_s[ci] - r_p[pi][None]
            dv = v_s[ci] - v_p[pi][None]
            d = np.linalg.norm(dr, axis=2)
            speed = np.linalg.norm(dv, axis=2)
            # Interior local minima whose true minimum could be within the threshold.
            mid = d[:, 1:-1]
            is_min = (mid <= d[:, :-2]) & (mid < d[:, 2:])
            bound = threshold_km + 0.5 * speed[:, 1:-1] * step_s + 1.0
            hit_rows, hit_cols = np.nonzero(is_min & (mid < bound) & (speed[:, 1:-1] > MIN_RELATIVE_SPEED))
            for hr, hc in zip(hit_rows, hit_cols, strict=True):
                j = hc + 1
                # Straight-line relative motion first: most minima are clearly too far.
                tau = -float(dr[hr, j] @ dv[hr, j]) / max(float(dv[hr, j] @ dv[hr, j]), 1e-12)
                tau = float(np.clip(tau, -step_s, step_s))
                if np.linalg.norm(dr[hr, j] + tau * dv[hr, j]) > threshold_km + 5:
                    continue
                s = sats[ci[hr]]
                guess = start + timedelta(seconds=float(times[j]) * step_s + tau)
                tca, miss, rel = _refine(p, s, guess, step_s)
                if miss <= threshold_km and start <= tca <= start + timedelta(hours=hours):
                    found.append(Approach(
                        p.norad_id, s.norad_id, p.name, s.name, tca, miss, rel,
                        _age_h(p, tca), _age_h(s, tca),
                    ))
    return found


def _age_h(sat: Satellite, when: datetime) -> float:
    m = sat.sat.model
    return (sum(_jd(when)) - (m.jdsatepoch + m.jdsatepochF)) * 24


def screen(
    primaries: list[Satellite],
    catalog: Catalog,
    start: datetime | None = None,
    hours: float = 72,
    threshold_km: float = 10.0,
) -> list[Approach]:
    """Every approach closer than ``threshold_km`` between a primary and anything in ``catalog``,
    nearest first. A pair of primaries is reported once."""
    start = (start or datetime.now(UTC)).astimezone(UTC)
    others = list(catalog)
    bands = np.array([band_km(s) for s in others]) if others else np.empty((0, 2))
    groups: dict[float, list[Satellite]] = {}
    for p in primaries:
        groups.setdefault(step_for(p), []).append(p)
    found = []
    for step_s, group in groups.items():
        found += _screen_group(group, others, bands, start, hours, threshold_km, step_s)
    unique = {}
    for a in found:  # the same encounter seen from both primaries, or from both sides of a chunk edge
        key = (min(a.primary, a.secondary), max(a.primary, a.secondary), round(a.tca.timestamp() / 30))
        if key not in unique or a.miss_km < unique[key].miss_km:
            unique[key] = a
    return sorted(unique.values(), key=lambda a: a.miss_km)


def altitude_km(sat: Satellite) -> float:
    lo, hi = band_km(sat)
    return (lo + hi) / 2 - EARTH_RADIUS_KM
