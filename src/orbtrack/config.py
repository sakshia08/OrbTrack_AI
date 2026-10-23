"""Runtime settings, read from environment variables with sensible defaults."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Observer:
    """A place on Earth that looks up at the sky."""

    name: str
    latitude: float  # degrees, north positive
    longitude: float  # degrees, east positive
    elevation_m: float = 0.0


# Placeholder until a home location is chosen (an open question in docs/PRD.md).
DEFAULT_OBSERVER = Observer("Bangalore", 12.9716, 77.5946, 920.0)


def cache_dir() -> Path:
    """Directory for downloaded catalogs and ephemerides (ORBTRACK_CACHE overrides)."""
    path = Path(os.environ.get("ORBTRACK_CACHE", Path.home() / ".cache" / "orbtrack"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def home_observer() -> Observer:
    """The observer from ORBTRACK_HOME="lat,lon[,elevation_m]", else the default."""
    raw = os.environ.get("ORBTRACK_HOME")
    if not raw:
        return DEFAULT_OBSERVER
    parts = [float(p) for p in raw.split(",")]
    return Observer("Home", parts[0], parts[1], parts[2] if len(parts) > 2 else 0.0)
