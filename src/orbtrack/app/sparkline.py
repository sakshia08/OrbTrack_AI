"""A small inline SVG chart of a satellite's altitude history for the follow card."""

import numpy as np

WIDTH, HEIGHT, PAD = 264, 58, 4


def altitude_sparkline(epochs: np.ndarray, altitude_km: np.ndarray) -> str | None:
    """Mean altitude over time as a glowing line with a soft fill, or None with too few points.

    Reboosts show up as steps up, drag as a slow slide down.
    """
    ok = np.isfinite(altitude_km)
    epochs, altitude_km = epochs[ok], altitude_km[ok]
    if len(altitude_km) < 3:
        return None
    t = (epochs - epochs[0]) / np.timedelta64(1, "s")
    span_t = max(float(t[-1]), 1.0)
    lo, hi = float(altitude_km.min()), float(altitude_km.max())
    margin = max((hi - lo) * 0.15, 0.5)
    lo, hi = lo - margin, hi + margin
    x = PAD + (t / span_t) * (WIDTH - 2 * PAD)
    y = PAD + (1 - (altitude_km - lo) / (hi - lo)) * (HEIGHT - 2 * PAD)
    line = " ".join(f"{a:.1f},{b:.1f}" for a, b in zip(x, y, strict=True))
    area = f"{x[0]:.1f},{HEIGHT} {line} {x[-1]:.1f},{HEIGHT}"
    return (
        f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" width="100%" height="{HEIGHT}" preserveAspectRatio="none">'
        '<defs><linearGradient id="spark-fill" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#38e1ff" stop-opacity="0.32"/>'
        '<stop offset="1" stop-color="#38e1ff" stop-opacity="0"/></linearGradient>'
        '<filter id="spark-glow"><feGaussianBlur stdDeviation="1.6"/></filter></defs>'
        f'<polygon points="{area}" fill="url(#spark-fill)"/>'
        f'<polyline points="{line}" fill="none" stroke="#38e1ff" stroke-width="2.4" stroke-opacity="0.5" filter="url(#spark-glow)"/>'
        f'<polyline points="{line}" fill="none" stroke="#bff6ff" stroke-width="1.2" stroke-linejoin="round"/>'
        f'<circle cx="{x[-1]:.1f}" cy="{y[-1]:.1f}" r="2.6" fill="#e8fbff"/>'
        "</svg>"
    )


def sparkline_caption(epochs: np.ndarray, altitude_km: np.ndarray) -> str:
    """e.g. "90 days · 412–421 km · 214 element sets"."""
    days = (epochs[-1] - epochs[0]) / np.timedelta64(1, "D")
    return (
        f"{days:.0f} days · {np.nanmin(altitude_km):,.0f}–{np.nanmax(altitude_km):,.0f} km"
        f" · {len(altitude_km):,} element sets"
    )
