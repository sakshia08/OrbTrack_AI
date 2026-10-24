"""Features for each pair of consecutive element sets, measured against the satellite's own normal.

For each step from one element set to the next we ask: how far did the semimajor axis,
inclination and eccentricity move, beyond the usual drift, in units of this satellite's
usual scatter (a robust z-score from its neighbouring steps)? And did the change stick?
A burn is a step that persists; a bad fit is a spike that snaps back on the next set.
"""

import warnings

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

WINDOW = 12  # neighbouring steps on each side that define "normal"
MIN_POINTS = 8  # fewer element sets than this and there is no baseline yet
# Scatter floors, about the precision TLEs are published with.
FLOORS = {"semimajor_km": 0.005, "inclination": 1e-4, "eccentricity": 1e-6}

NAMES = [
    "abs_z_a", "persist_a", "next_a", "prev_a",
    "abs_z_i", "persist_i", "next_i", "prev_i",
    "abs_z_e", "persist_e",
    "gap_ratio",
]


def _nanmedian(x: np.ndarray, axis: int = 1) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN rows at the ends
        return np.nanmedian(x, axis=axis)


def _neighbours(x: np.ndarray) -> np.ndarray:
    """For each step, its neighbours within WINDOW, leaving out itself and the steps next to it
    (a burn caught in two fits spreads over two steps)."""
    pad = np.full(WINDOW, np.nan)
    win = sliding_window_view(np.concatenate([pad, x, pad]), 2 * WINDOW + 1).copy()
    win[:, WINDOW - 1 : WINDOW + 2] = np.nan
    return win


def _element(x: np.ndarray, t: np.ndarray, floor: float) -> dict[str, np.ndarray]:
    """Robust z-scores of one element's steps, and how much of each step persisted."""
    d, dt = np.diff(x), np.maximum(np.diff(t), 1e-3)
    rate = _nanmedian(_neighbours(d / dt))
    rate = np.where(np.isfinite(rate), rate, 0.0)
    residual = d - rate * dt
    win = _neighbours(residual)
    scale = 1.4826 * _nanmedian(np.abs(win - _nanmedian(win)[:, None]))
    scale = np.fmax(scale, floor)  # NaN (no neighbours) falls back to the floor too

    # Level after the step minus level before, both carried to the time of the set after it.
    m = len(d)
    k = np.arange(m)
    xp = np.concatenate([[np.nan] * 2, x, [np.nan] * 3])
    tp = np.concatenate([[np.nan] * 2, t, [np.nan] * 3])
    t_after = t[k + 1]
    before = np.stack([xp[k + 2 + j] + rate * (t_after - tp[k + 2 + j]) for j in (-2, -1, 0)], axis=1)
    after = np.stack([xp[k + 2 + j] - rate * (tp[k + 2 + j] - t_after) for j in (1, 2, 3)], axis=1)
    persisted = _nanmedian(after) - _nanmedian(before)

    z = residual / scale
    sign = np.where(z >= 0, 1.0, -1.0)
    nxt = np.concatenate([z[1:], [np.nan]])
    prv = np.concatenate([[np.nan], z[:-1]])
    return {
        "z": z, "abs_z": np.abs(z), "scale": scale, "persisted": persisted,
        "persist_z": persisted / scale * sign,  # same sign as the step if it stuck
        "next": nxt * sign, "prev": prv * sign,  # strongly negative when a spike snaps back
    }


def pair_features(series: dict[str, np.ndarray]) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """A feature row per step (len(epoch) - 1 rows), plus the physical numbers behind them."""
    epochs = series["epoch"]
    if len(epochs) < MIN_POINTS:
        return np.empty((0, len(NAMES))), {}
    t = (epochs - epochs[0]) / np.timedelta64(1, "D")
    a = _element(series["semimajor_km"], t, FLOORS["semimajor_km"])
    i = _element(series["inclination"], t, FLOORS["inclination"])
    e = _element(series["eccentricity"], t, FLOORS["eccentricity"])
    dt = np.diff(t)
    X = np.column_stack([
        a["abs_z"], a["persist_z"], a["next"], a["prev"],
        i["abs_z"], i["persist_z"], i["next"], i["prev"],
        e["abs_z"], e["persist_z"],
        dt / max(float(np.median(dt)), 1e-3),
    ])
    info = {
        "delta_a_km": a["persisted"], "scale_a_km": a["scale"], "z_a": a["z"],
        "delta_i_deg": i["persisted"], "scale_i_deg": i["scale"], "z_i": i["z"],
    }
    return X, info
