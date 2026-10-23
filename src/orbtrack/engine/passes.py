"""Passes of a satellite over an observer, with naked-eye visibility."""

from dataclasses import dataclass
from datetime import datetime
from functools import cache

import numpy as np
from skyfield.api import Loader, Time, wgs84

from orbtrack.config import Observer, cache_dir
from orbtrack.engine.catalog import Satellite

# A pass is visible to the eye when the satellite is sunlit and the observer's
# sky is dark: the Sun at least 6° below the horizon (civil twilight).
SUN_DARK_ALTITUDE_DEG = -6.0
VISIBILITY_SAMPLES = 20


@dataclass(frozen=True)
class Pass:
    rise: datetime  # UTC
    culminate: datetime
    set: datetime
    max_elevation_deg: float
    rise_azimuth_deg: float
    set_azimuth_deg: float
    visible: bool | None  # None when visibility was not computed

    @property
    def duration_s(self) -> float:
        return (self.set - self.rise).total_seconds()


@cache
def ephemeris():
    """JPL DE421 (Sun, Earth), downloaded once (~17 MB) into the cache directory."""
    return Loader(str(cache_dir() / "skyfield"), verbose=False)("de421.bsp")


def next_passes(
    satellite: Satellite,
    observer: Observer,
    start: Time,
    hours: float = 24,
    min_elevation_deg: float = 10.0,
    with_visibility: bool = True,
) -> list[Pass]:
    """Complete passes that rise above ``min_elevation_deg`` within ``hours`` of ``start``.

    Passes already in progress at ``start`` or unfinished at the window's end are left out.
    """
    ts = start.ts
    topos = wgs84.latlon(observer.latitude, observer.longitude, observer.elevation_m)
    end = ts.tt_jd(start.tt + hours / 24)
    times, kinds = satellite.sat.find_events(topos, start, end, altitude_degrees=min_elevation_deg)

    passes = []
    rise = None
    culminations: list[Time] = []
    for t, kind in zip(times, kinds, strict=True):
        if kind == 0:
            rise, culminations = t, []
        elif kind == 1 and rise is not None:
            culminations.append(t)
        elif kind == 2 and rise is not None:
            passes.append(_build_pass(satellite, topos, rise, culminations, t, with_visibility))
            rise = None
    return passes


def _build_pass(satellite, topos, rise, culminations, set_, with_visibility) -> Pass:
    relative = satellite.sat - topos
    culm_alts = [relative.at(c).altaz()[0].degrees for c in culminations] or [0.0]
    peak = culminations[int(np.argmax(culm_alts))] if culminations else rise
    return Pass(
        rise=rise.utc_datetime(),
        culminate=peak.utc_datetime(),
        set=set_.utc_datetime(),
        max_elevation_deg=float(max(culm_alts)),
        rise_azimuth_deg=float(relative.at(rise).altaz()[1].degrees),
        set_azimuth_deg=float(relative.at(set_).altaz()[1].degrees),
        visible=_is_visible(satellite, topos, rise, set_) if with_visibility else None,
    )


def _is_visible(satellite, topos, rise: Time, set_: Time) -> bool:
    eph = ephemeris()
    ts = rise.ts
    samples = ts.tt_jd(np.linspace(rise.tt, set_.tt, VISIBILITY_SAMPLES))
    sunlit = satellite.sat.at(samples).is_sunlit(eph)
    sun_alt = (eph["earth"] + topos).at(samples).observe(eph["sun"]).apparent().altaz()[0].degrees
    return bool(np.any(sunlit & (sun_alt < SUN_DARK_ALTITUDE_DEG)))
