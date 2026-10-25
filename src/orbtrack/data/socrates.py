"""Checking our screening against CelesTrak's SOCRATES Plus close-approach predictions.

SOCRATES screens the whole catalog every day and, for each conjunction, publishes the exact
element sets it used. Recomputing those same pairs from those same element sets is a clean
test: any difference is ours, not a difference in input data.
"""

import csv
import io
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

BASE = "https://celestrak.org/SOCRATES"
PAUSE_S = 1.0  # between requests: CelesTrak is run by one person on donated bandwidth

_ROW = re.compile(
    r"openWindowData\('data\.php\?CATNR=(\d+),(\d+)'\).*?"
    r"<td>\d+</td>\s*<td>([^<]*)</td>\s*<td>[\d.]+</td>\s*<td rowspan=2>([^<]+)</td>\s*"
    r"<td>([\d.]+)</td>\s*<td\s*>([\d.]+)</td>.*?"
    r"<td>\d+</td>\s*<td>([^<]*)</td>",
    re.DOTALL,
)


@dataclass
class Conjunction:
    first: int
    second: int
    first_name: str
    second_name: str
    tca: datetime
    range_km: float
    speed_km_s: float


@dataclass
class Check:
    conjunction: Conjunction
    tca_diff_s: float | None  # ours minus SOCRATES; None if we found nothing
    range_diff_m: float | None
    speed_diff_m_s: float | None

    @property
    def matched(self) -> bool:
        return self.tca_diff_s is not None and abs(self.tca_diff_s) < 1 and abs(self.range_diff_m) < 50


def _name(raw: str) -> str:
    return re.sub(r"\s*\[.*?\]\s*$", "", raw).strip()


def parse_table(html: str) -> list[Conjunction]:
    out = []
    for a, b, n1, tca, rng, speed, n2 in _ROW.findall(html):
        when = datetime.strptime(tca.strip(), "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC)
        out.append(Conjunction(int(a), int(b), _name(n1), _name(n2), when, float(rng), float(speed)))
    return out


def parse_elements(text: str) -> list[dict]:
    """The OMM CSV inside SOCRATES' "GP Data" page."""
    plain = re.sub(r"<[^>]+>", "\n", text)
    start = plain.find("OBJECT_NAME,")
    if start < 0:
        return []
    lines = [ln for ln in plain[start:].splitlines() if ln.count(",") >= 10]
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def _get(client: httpx.Client, url: str) -> str:
    time.sleep(PAUSE_S)
    response = client.get(url)
    response.raise_for_status()
    return response.text


def fetch_table(client: httpx.Client, norad_id: int, count: int) -> list[Conjunction]:
    return parse_table(_get(client, f"{BASE}/table-socrates.php?CATNR={norad_id}&ORDER=MINRANGE&MAX={count}"))


def fetch_elements(client: httpx.Client, first: int, second: int) -> list[dict]:
    return parse_elements(_get(client, f"{BASE}/data.php?CATNR={first},{second}"))


def recompute(c: Conjunction, records: list[dict]) -> Check:
    """Screen just this pair, from the same element sets, around SOCRATES' TCA."""
    from orbtrack.engine import Catalog
    from orbtrack.engine.conjunctions import screen

    catalog = Catalog(records)
    first = catalog.get(c.first)
    if first is None or catalog.get(c.second) is None:
        return Check(c, None, None, None)
    found = screen([first], catalog, c.tca - timedelta(minutes=20), hours=40 / 60, threshold_km=c.range_km + 2)
    if not found:
        return Check(c, None, None, None)
    best = min(found, key=lambda a: abs((a.tca - c.tca).total_seconds()))
    return Check(c, (best.tca - c.tca).total_seconds(), (best.miss_km - c.range_km) * 1000,
                 (best.speed_km_s - c.speed_km_s) * 1000)


def check(norad_ids: list[int], per_satellite: int = 5, client: httpx.Client | None = None) -> list[Check]:
    """The ``per_satellite`` closest SOCRATES conjunctions for each satellite, recomputed by us."""
    client = client or httpx.Client(timeout=90, follow_redirects=True)
    out = []
    for norad_id in norad_ids:
        # Docked and formation-flying pairs (relative speed ~0) are skipped by our screening on purpose.
        moving = [c for c in fetch_table(client, norad_id, 3 * per_satellite + 10) if c.speed_km_s >= 0.01]
        for c in moving[:per_satellite]:
            out.append(recompute(c, fetch_elements(client, c.first, c.second)))
    return out
