import numpy as np

from orbtrack.app.sparkline import altitude_sparkline, sparkline_caption


def test_sparkline_needs_a_few_points():
    epochs = np.array(["2026-01-01", "2026-01-02"], dtype="datetime64[us]")
    assert altitude_sparkline(epochs, np.array([410.0, 411.0])) is None


def test_sparkline_draws_the_series_and_caption():
    epochs = np.arange("2026-01-01", "2026-03-31", dtype="datetime64[D]").astype("datetime64[us]")
    alt = 420 - 0.05 * np.arange(len(epochs)) + (np.arange(len(epochs)) > 40) * 1.5
    svg = altitude_sparkline(epochs, alt)
    assert svg.startswith("<svg") and svg.count("<polyline") == 2
    assert sparkline_caption(epochs, alt) == f"88 days · {alt.min():.0f}–{alt.max():.0f} km · 89 element sets"
