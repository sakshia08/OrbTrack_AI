"""Detected events for the page: where on Earth they happened, replays, and a labelled demo set.

A replay shows the orbit just before an event and just after it, both rebuilt from the
stored element sets at the moment of the burn and frozen against the Earth as it was then.
A 1.5 km reboost is invisible on a globe 100 units across, so the difference between the
two orbits (its radial and out-of-plane parts) is drawn larger, and the page says by how much.
"""

import math
from datetime import UTC, datetime, timedelta

import numpy as np
from sgp4 import omm
from sgp4.api import Satrec
from skyfield.sgp4lib import theta_GMST1982

from orbtrack.app.frames import GLOBE_RADIUS, to_globe_axes
from orbtrack.data.history import MU_KM3_S2
from orbtrack.engine.propagate import ecef_to_geodetic

KIND_COLORS = {"orbit raise": "#5dffb0", "orbit lower": "#ff9a4b", "plane change": "#b48cff", "decay": "#ff4d6d"}
RING_POINTS = 240
TARGET_GAP = 0.08  # the widest gap between the drawn orbits, as a share of the orbit's radius
OMM_DEFAULTS = {"CLASSIFICATION_TYPE": "U", "OBJECT_ID": "2000-000A", "EPHEMERIS_TYPE": 0, "REV_AT_EPOCH": 0,
                "ELEMENT_SET_NO": 999, "BSTAR": 0.0, "MEAN_MOTION_DOT": 0.0, "MEAN_MOTION_DDOT": 0.0}


def satrec(record: dict) -> Satrec:
    sat = Satrec()
    omm.initialize(sat, {**OMM_DEFAULTS, **{k: v for k, v in record.items() if v is not None}})
    return sat


def _jd(when: datetime) -> tuple[float, float]:
    """UTC Julian date split in two, as SGP4 wants it."""
    days = (when - datetime(2000, 1, 1, 12, tzinfo=UTC)) / timedelta(days=1)
    whole = math.floor(days)
    return 2451545.0 + whole, days - whole


def _orbit(sat: Satrec, start: datetime, points: int = RING_POINTS) -> np.ndarray:
    """One revolution of TEME positions (km) from ``start``, ``points + 1`` samples (closed)."""
    period_s = 2 * math.pi / sat.no_kozai * 60
    jd, fr = _jd(start)
    fr = fr + np.linspace(0, period_s, points + 1) / 86400
    errors, r, _ = sat.sgp4_array(np.full_like(fr, jd), fr)
    r[errors != 0] = np.nan
    return r


def _teme_to_ecef(r: np.ndarray, theta: float) -> np.ndarray:
    c, s = math.cos(theta), math.sin(theta)
    return np.stack([c * r[..., 0] + s * r[..., 1], -s * r[..., 0] + c * r[..., 1], r[..., 2]], axis=-1)


def _gmst(when: datetime) -> float:
    jd, fr = _jd(when)
    return float(theta_GMST1982(jd, fr)[0])


def burn_time(event: dict) -> datetime:
    """Halfway between the last element set before the change and the first one after it."""
    before, after = (_utc(event["before_epoch"] or event["epoch"]), _utc(event["epoch"]))
    return before + (after - before) / 2


def _utc(when: datetime) -> datetime:
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)


def where(record: dict, when: datetime) -> tuple[float, float, float]:
    """Latitude, longitude (degrees) and height (km) of the satellite at ``when``."""
    r = _orbit(satrec(record), when, points=1)[0]
    lat, lon, height = ecef_to_geodetic(*_teme_to_ecef(r, _gmst(when)))
    return float(lat), float(lon), float(height)


def _nice(k: float) -> int:
    """Round an exaggeration down to 1, 2 or 5 times a power of ten."""
    if k <= 1:
        return 1
    exp = 10 ** math.floor(math.log10(k))
    return int(max(m for m in (1, 2, 5) if m * exp <= k) * exp)


def replay(item: dict) -> dict:
    """Both orbits around the moment of the event, in globe axes frozen to the Earth at that moment."""
    when = item["burn_at"]
    before = _orbit(satrec(item["before"]), when)
    after = _orbit(satrec(item["after"]), when)
    radial = before / np.linalg.norm(before, axis=1, keepdims=True)
    normal = np.cross(before[0], before[RING_POINTS // 4])
    normal /= np.linalg.norm(normal)
    # Compare the orbits at the same angle around the old one, not at the same time: two element
    # sets can disagree on where along the orbit the satellite is, but that is not the change.
    # Angles are unwrapped (0 to just over 2π) because the plane drifts a little each revolution,
    # so the end of a ring is not quite its start.
    e1 = radial[0]
    e2 = np.cross(normal, e1)

    def angle(r: np.ndarray) -> np.ndarray:
        a = np.unwrap(np.arctan2(r @ e2, r @ e1))
        return a - 2 * np.pi * np.round(a[0] / (2 * np.pi))

    ok = np.isfinite(after).all(axis=1)
    at = lambda values: np.interp(angle(before), angle(after[ok]), values[ok])
    dr = at(np.linalg.norm(after, axis=1)) - np.linalg.norm(before, axis=1)
    dn = at(after @ normal) - before @ normal
    gap = np.nanmax(np.hypot(dr, dn))
    mean_r = float(np.nanmean(np.linalg.norm(before, axis=1)))
    scale = _nice(TARGET_GAP * mean_r / gap) if gap > 1e-6 else 1
    shown = before + scale * (dr[:, None] * radial + dn[:, None] * normal)

    theta = _gmst(when)
    to_scene = lambda r: np.nan_to_num(to_globe_axes(_teme_to_ecef(r, theta))).round(3)
    lat, lon, height = ecef_to_geodetic(*_teme_to_ecef(before[0], theta))
    far = int(np.nanargmax(np.hypot(dr, dn)))
    return {
        "key": item["key"],
        "kind": item["kind"],
        "color": KIND_COLORS[item["kind"]],
        "before": to_scene(before).tolist(),
        "after": to_scene(shown).tolist(),
        "far": far,
        "normal": to_globe_axes(_teme_to_ecef(normal, theta)).round(4).tolist(),
        "radius": mean_r / 6371.0 * GLOBE_RADIUS,
        "lat": float(lat), "lon": float(lon), "height_km": float(height),
        "scale": scale,
        "burn_label": f"{item['kind'].upper()} · {when:%d %b %H:%M} UTC",
        "demo": item.get("demo", False),
    }


# --- The list the page shows ----------------------------------------------------------


def size_text(event: dict) -> str:
    if event["kind"] == "plane change":
        return f"{event['delta_i_deg']:+.3f}°"
    if event["kind"] == "decay":
        return "re-entry"
    return f"{event['delta_a_km']:+.2f} km"


def ago_text(when: datetime, now: datetime) -> str:
    hours = (now - when).total_seconds() / 3600
    if hours < 1:
        return "now"
    if hours < 36:
        return f"{hours:.0f} h"
    return f"{hours / 24:.0f} d"


def _item(event: dict, before: dict, after: dict, demo: bool = False, when: datetime | None = None) -> dict:
    when = when or burn_time(event)
    lat, lon, _ = where(before, when)
    return {
        **event,
        "key": f"{event['norad_id']}-{int(when.timestamp())}-{event['kind'].replace(' ', '-')}",
        "burn_at": when, "ms": when.timestamp() * 1000, "lat": lat, "lon": lon,
        "before": before, "after": after, "demo": demo, "color": KIND_COLORS[event["kind"]],
    }


def load(store, days: int = 30) -> list[dict]:
    """Stored events of the last ``days``, newest first, with the element sets to replay them."""
    out = []
    for event in store.events(since=datetime.now(UTC) - timedelta(days=days)):
        before_epoch = event["before_epoch"] or event["epoch"]
        sets = store.element_sets(event["norad_id"], [before_epoch, event["epoch"]])
        if before_epoch in sets and event["epoch"] in sets:
            out.append(_item(event, sets[before_epoch], sets[event["epoch"]]))
    return out


# --- Demo events ---------------------------------------------------------------------

# (NORAD id, kind, size, days ago, where along the orbit: degrees past the ascending node).
# Sizes are typical of each satellite's real maneuvers.
DEMO = [
    (25544, "orbit raise", 1.6, 1.3, 70),  # ISS reboost: lands on the time machine bar
    (51850, "orbit lower", -0.9, 4.6, 200),  # GOES 18 east-west station-keeping
    (49260, "plane change", 0.022, 8.2, 0),  # Landsat 9 inclination trim, at the node
    (48274, "orbit raise", 2.1, 12.5, 130),  # Tiangong reboost
    (43013, "orbit raise", 0.35, 19.0, 300),  # NOAA 20 drag make-up
]


def _semimajor(mean_motion: float) -> float:
    return (MU_KM3_S2 / (mean_motion * 2 * math.pi / 86400) ** 2) ** (1 / 3)


def _mean_motion(a_km: float) -> float:
    return math.sqrt(MU_KM3_S2 / a_km**3) * 86400 / (2 * math.pi)


def burned(record: dict, when: datetime, delta_a_km: float = 0.0, delta_i_deg: float = 0.0,
           u_deg: float = 0.0) -> tuple[dict, dict]:
    """A before/after pair of element sets for a small burn at ``when``, ``u_deg`` past the node.

    A raise makes the burn point the new perigee (a lower, the new apogee), so the
    eccentricity vector moves by Δa/a towards it. Plane changes belong at the node (u = 0).
    """
    e = float(record["ECCENTRICITY"])
    w = math.radians(float(record["ARG_OF_PERICENTER"]))
    u = math.radians(u_deg)
    m = (u - w) - 2 * e * math.sin(u - w)  # small-eccentricity mean anomaly for that spot
    epoch = when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")
    before = {**record, "EPOCH": epoch, "MEAN_ANOMALY": math.degrees(m) % 360}
    a = _semimajor(float(record["MEAN_MOTION"]))
    ex, ey = e * math.cos(w) + delta_a_km / a * math.cos(u), e * math.sin(w) + delta_a_km / a * math.sin(u)
    e2, w2 = math.hypot(ex, ey), math.atan2(ey, ex)
    nu = u - w2
    after = {
        **before,
        "MEAN_MOTION": _mean_motion(a + delta_a_km),
        "ECCENTRICITY": e2,
        "ARG_OF_PERICENTER": math.degrees(w2) % 360,
        "MEAN_ANOMALY": math.degrees(nu - 2 * e2 * math.sin(nu)) % 360,
        "INCLINATION": float(record["INCLINATION"]) + delta_i_deg,
    }
    return before, after


def demo(records: list[dict], now: datetime | None = None) -> list[dict]:
    """Made-up but realistic events on real watchlist satellites, marked as demo everywhere."""
    now = now or datetime.now(UTC)
    by_id = {int(float(r["NORAD_CAT_ID"])): r for r in records}
    out = []
    for norad_id, kind, size, days_ago, u_deg in DEMO:
        record = by_id.get(norad_id)
        if record is None:
            continue
        when = (now - timedelta(days=days_ago)).replace(microsecond=0)
        da, di = (0.0, size) if kind == "plane change" else (size, 0.0)
        before, after = burned(record, when, da, di, u_deg)
        verb = f"inclination moved {di:+.3f}°" if di else f"mean altitude {'rose' if da > 0 else 'fell'} {abs(da):.2f} km"
        event = {
            "norad_id": norad_id, "name": record["OBJECT_NAME"], "kind": kind,
            "before_epoch": when - timedelta(hours=3), "epoch": when + timedelta(hours=3),
            "delta_a_km": da, "delta_i_deg": di, "confidence": 0.97,
            "reason": f"DEMO · {verb}: a made-up event to show what a detection looks like",
        }
        out.append(_item(event, before, after, demo=True, when=when))
    return out


def strip_svg(items: list[dict], now: datetime, days: int = 30) -> str:
    """The last ``days`` as a thin strip: weekly ticks, one glowing mark per event, "now" at the right."""
    w, h, pad = 212, 30, 6
    x = lambda when: pad + (1 - (now - when).total_seconds() / (days * 86400)) * (w - 2 * pad)
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}">',
             f'<rect x="{pad}" y="13" width="{w - 2 * pad}" height="1" fill="rgba(200,225,255,0.18)"/>']
    for week in range(0, days + 1, 7):
        px = x(now - timedelta(days=week))
        parts.append(f'<rect x="{px:.1f}" y="10" width="1" height="7" fill="rgba(200,225,255,0.22)"/>')
    parts.append(f'<text x="{pad}" y="28" class="lbl">{days} D AGO</text>'
                 f'<text x="{w - pad}" y="28" class="lbl" text-anchor="end">NOW</text>')
    for e in sorted(items, key=lambda e: e["burn_at"]):
        px = x(e["burn_at"])
        if pad - 2 <= px <= w - pad + 2:
            parts.append(f'<circle cx="{px:.1f}" cy="13.5" r="5.5" fill="{e["color"]}" opacity="0.22"/>'
                         f'<circle cx="{px:.1f}" cy="13.5" r="2.6" fill="{e["color"]}"/>')
    parts.append(f'<rect x="{w - pad - 1}" y="7" width="2" height="13" rx="1" fill="#ff4d6d"/>')
    return "".join(parts) + "</svg>"
