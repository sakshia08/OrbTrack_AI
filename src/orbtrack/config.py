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


# Satellites whose history is backfilled from Space-Track. More can be added with
# `orbtrack history watch <name>`. Only these are kept in history, so it stays small.
DEFAULT_WATCHLIST = [
    25544,  # ISS: regular reboosts
    48274,  # Tiangong (CSS Tianhe)
    20580,  # Hubble
    41866,  # GOES 16: geostationary station-keeping
    43226,  # GOES 17
    51850,  # GOES 18
    40732,  # Meteosat 11
    41836,  # Himawari 9
    43013,  # NOAA 20: sun-synchronous orbit maintenance
    37849,  # Suomi NPP
    39084,  # Landsat 8
    49260,  # Landsat 9
    36508,  # CryoSat 2
]


def data_dir() -> Path:
    """Directory for data worth keeping, such as orbit history (ORBTRACK_DATA overrides)."""
    path = Path(os.environ.get("ORBTRACK_DATA", Path.home() / ".local" / "share" / "orbtrack"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=value lines from a .env file; blank lines, comments and quotes handled simply."""
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def spacetrack_credentials(env_file: Path | None = None) -> tuple[str, str] | None:
    """Space-Track login from SPACETRACK_IDENTITY / SPACETRACK_PASSWORD, or from a .env file."""
    env_file = env_file or Path.cwd() / ".env"
    values = {**read_env_file(env_file), **os.environ}
    identity, password = values.get("SPACETRACK_IDENTITY"), values.get("SPACETRACK_PASSWORD")
    return (identity, password) if identity and password else None


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
