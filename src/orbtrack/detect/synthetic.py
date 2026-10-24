"""Simulated element-set histories with known maneuvers, for training and grading the detector.

Public maneuver labels are rare, so the detector learns from histories that look like real
TLE streams: irregular update times, scatter between fits, drag that waxes and wanes, the
odd bad fit, long gaps, and fits that only half-catch a burn the first time round. Each
burn's place in the stream is recorded, so precision and recall can be measured.

Numbers are rough but grounded: ISS reboosts lift the orbit 0.5–3 km a few times a quarter,
geostationary east-west station-keeping moves the semimajor axis a kilometre or so every
couple of weeks and north-south burns trim ~0.05° of inclination, and TLE scatter in the
mean semimajor axis is tens of metres.
"""

from dataclasses import dataclass, field

import numpy as np

EPOCH0 = np.datetime64("2026-01-01T00:00:00", "us")


@dataclass(frozen=True)
class Profile:
    name: str
    a_km: tuple[float, float]  # mean semimajor axis
    inc_deg: tuple[float, float]
    ecc: tuple[float, float]
    cadence_h: tuple[float, float]  # typical hours between element sets
    noise_a_km: tuple[float, float]  # 1-sigma fit-to-fit scatter
    noise_i_deg: tuple[float, float]
    noise_e: tuple[float, float]
    drift_km_day: tuple[float, float]  # drag (positive = sinking)
    inc_drift_deg_day: tuple[float, float]
    burns_per_30d: float
    burn_a_km: tuple[float, float]  # size of a semimajor-axis change (log-uniform)
    lower_share: float  # share of in-plane burns that lower the orbit
    plane_share: float  # share of burns that change inclination instead
    burn_i_deg: tuple[float, float] = (0.0, 0.0)


PROFILES = {
    "station": Profile("station", (6765, 6800), (41.4, 51.7), (2e-4, 1e-3), (3, 8), (0.015, 0.05),
                       (2e-4, 8e-4), (1e-5, 4e-5), (0.03, 0.12), (0, 0), 1.2, (0.5, 3.0), 0.15, 0.0),
    "leo": Profile("leo", (6880, 7200), (70, 99), (1e-4, 2e-3), (6, 20), (0.01, 0.04),
                   (2e-4, 1e-3), (5e-6, 3e-5), (0.0005, 0.02), (0, 0), 0.6, (0.1, 1.2), 0.2, 0.25,
                   (0.004, 0.04)),
    "geo": Profile("geo", (42160, 42170), (0.01, 0.12), (5e-5, 4e-4), (10, 30), (0.01, 0.05),
                   (3e-4, 1.5e-3), (5e-6, 3e-5), (-0.01, 0.01), (0.001, 0.003), 2.0, (0.25, 2.0), 0.5, 0.3,
                   (0.015, 0.1)),
}

OUTLIER_RATE = 0.03  # share of element sets that are a bad fit
LONG_GAP_RATE = 0.02  # share of gaps that last two to four days
PARTIAL_RATE = 0.4  # share of burns the next fit only partly catches


@dataclass
class Burn:
    pair: int  # the burn happened between element set `pair` and `pair + 1`
    kind: str  # "raise", "lower" or "plane"
    size: float  # km for raise/lower, degrees for plane
    partial: bool  # the first fit afterwards caught only part of it


@dataclass
class SimulatedSatellite:
    profile: str
    series: dict[str, np.ndarray]
    burns: list[Burn] = field(default_factory=list)


def _uniform(rng, bounds):
    return rng.uniform(*bounds)


def _log_uniform(rng, bounds):
    return float(np.exp(rng.uniform(np.log(bounds[0]), np.log(bounds[1]))))


def simulate(profile: Profile, days: float, rng: np.random.Generator) -> SimulatedSatellite:
    cadence = _uniform(rng, profile.cadence_h) / 24
    gaps = []
    while sum(gaps) < days:
        gap = cadence * rng.gamma(4.0) / 4
        if rng.random() < LONG_GAP_RATE:
            gap = rng.uniform(2, 4)
        gaps.append(max(gap, 0.02))
    t = np.concatenate([[0.0], np.cumsum(gaps)])
    t = t[t <= days]

    # Burn times, at least two days apart.
    burn_times: list[float] = []
    for bt in np.sort(rng.uniform(1, days - 0.5, rng.poisson(profile.burns_per_30d * days / 30))):
        if not burn_times or bt - burn_times[-1] > 2:
            burn_times.append(float(bt))

    a0, i0, e0 = _uniform(rng, profile.a_km), _uniform(rng, profile.inc_deg), _uniform(rng, profile.ecc)
    drift, inc_drift = _uniform(rng, profile.drift_km_day), _uniform(rng, profile.inc_drift_deg_day)
    phase = rng.uniform(0, 2 * np.pi)
    # Drag follows the Sun's 27-day rotation; integrate it on the element-set times.
    rate = drift * (1 + 0.4 * np.sin(2 * np.pi * t / 27 + phase))
    a = a0 - np.concatenate([[0.0], np.cumsum(0.5 * (rate[1:] + rate[:-1]) * np.diff(t))])
    inc = i0 + inc_drift * t
    ecc = np.full_like(t, e0)

    burns = []
    for bt in burn_times:
        k = int(np.searchsorted(t, bt)) - 1  # t[k] < bt <= t[k + 1]
        if k < 0 or k >= len(t) - 1:
            continue
        after = t >= bt
        partial = rng.random() < PARTIAL_RATE
        share = np.where(after, 1.0, 0.0)
        if partial:
            share[k + 1] = rng.uniform(0.3, 0.8)
        if rng.random() < profile.plane_share:
            size = _log_uniform(rng, profile.burn_i_deg) * rng.choice([-1, 1])
            inc = inc + share * size
            burns.append(Burn(k, "plane", size, partial))
        else:
            size = _log_uniform(rng, profile.burn_a_km) * (-1 if rng.random() < profile.lower_share else 1)
            a = a + share * size
            ecc = np.abs(ecc + share * abs(size) / a0 * rng.uniform(-1, 1))
            burns.append(Burn(k, "raise" if size > 0 else "lower", size, partial))

    n = len(t)
    bad = np.where(rng.random(n) < OUTLIER_RATE, rng.uniform(4, 10, n), 1.0)
    a_obs = a + _uniform(rng, profile.noise_a_km) * bad * rng.standard_normal(n)
    i_obs = inc + _uniform(rng, profile.noise_i_deg) * bad * rng.standard_normal(n)
    e_obs = np.abs(ecc + _uniform(rng, profile.noise_e) * bad * rng.standard_normal(n))
    series = {
        "epoch": EPOCH0 + (t * 86400e6).astype("timedelta64[us]"),
        "semimajor_km": a_obs,
        "inclination": i_obs,
        "eccentricity": e_obs,
    }
    return SimulatedSatellite(profile.name, series, burns)


def fleet(n: int, days: float = 120, seed: int = 0, mix: dict[str, float] | None = None) -> list[SimulatedSatellite]:
    """``n`` satellites drawn from the profiles (by default a third of each)."""
    rng = np.random.default_rng(seed)
    mix = mix or {name: 1 / len(PROFILES) for name in PROFILES}
    names = rng.choice(list(mix), size=n, p=np.array(list(mix.values())) / sum(mix.values()))
    return [simulate(PROFILES[name], days, rng) for name in names]
