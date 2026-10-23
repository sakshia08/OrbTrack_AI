"""CelesTrak GP client: current orbital elements as OMM JSON, cached on disk.

CelesTrak publishes new elements roughly every two hours and asks clients not
to download the same data more often than that, so cached files are reused
until they are older than ``max_age``.
"""

import json
import time
from pathlib import Path

import httpx

from orbtrack.config import cache_dir

GP_URL = "https://celestrak.org/NORAD/elements/gp.php"
DEFAULT_MAX_AGE_S = 2 * 3600


def fetch_group(
    group: str = "active",
    *,
    max_age_s: float = DEFAULT_MAX_AGE_S,
    directory: Path | None = None,
) -> list[dict]:
    """Return OMM records for a CelesTrak group, e.g. "active", "stations", "starlink".

    Falls back to a stale cached copy if the download fails.
    """
    path = (directory or cache_dir() / "celestrak") / f"{group}.json"
    if path.exists() and time.time() - path.stat().st_mtime < max_age_s:
        return json.loads(path.read_text())

    try:
        response = httpx.get(
            GP_URL,
            params={"GROUP": group, "FORMAT": "json"},
            timeout=60,
            follow_redirects=True,
        )
        response.raise_for_status()
        records = response.json()
    except (httpx.HTTPError, ValueError):
        if path.exists():
            return json.loads(path.read_text())
        raise

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records))
    return records
