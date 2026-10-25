"""Filling the history store: CelesTrak snapshots, Space-Track backfill, close-approach screening."""

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from orbtrack.data.celestrak import fetch_group
from orbtrack.data.history import HistoryStore
from orbtrack.data.spacetrack import SpaceTrack

log = logging.getLogger("orbtrack")

BACKFILL_DAYS = 365
# What close approaches are screened against. CelesTrak publishes no full catalog, so this is
# every active satellite plus the four big debris clouds it does publish; most other debris
# and spent rocket stages are missing (Space-Track's full catalog would fill that gap).
SCREEN_GROUPS = ["active", "cosmos-1408-debris", "fengyun-1c-debris", "iridium-33-debris", "cosmos-2251-debris"]
SCREEN_HOURS = 72
SCREEN_KM = 10.0
OVERLAP = timedelta(days=1)  # re-ask for the last day; the store ignores what it already has


@dataclass
class SyncResult:
    source: str
    satellites: int
    fetched: int
    added: int

    def __str__(self) -> str:
        return f"{self.source}: {self.fetched:,} element sets for {self.satellites:,} satellites, {self.added:,} new"


def snapshot(store: HistoryStore, records: list[dict], source: str = "celestrak") -> SyncResult:
    """Keep the watchlist's part of a catalog download, so history stays small (a few MB a year)."""
    watched = set(store.watchlist())
    records = [r for r in records if int(float(r.get("NORAD_CAT_ID", -1))) in watched]
    added = store.add(records, source)
    result = SyncResult(source, len({r.get("NORAD_CAT_ID") for r in records}), len(records), added)
    store.log_sync(source, result.satellites, result.fetched, result.added)
    return result


def sync_spacetrack(
    store: HistoryStore,
    credentials: tuple[str, str],
    *,
    days: int = BACKFILL_DAYS,
    now: datetime | None = None,
    client_factory=SpaceTrack,
) -> SyncResult:
    """Backfill new watchlist satellites, then fetch only what is new for the rest.

    Two queries at most (more only for watchlists over 100 satellites), which keeps
    a nightly run far inside Space-Track's limits.
    """
    now = now or datetime.now(UTC)
    ids = store.watchlist()
    latest = store.latest_epochs(ids, source="spacetrack")
    new = [n for n in ids if n not in latest]
    known = [n for n in ids if n in latest]
    records: list[dict] = []
    with client_factory(*credentials) as st:
        if new:
            records += st.gp_history(new, since=now - timedelta(days=days))
        if known:
            records += st.gp_history(known, since=min(latest[n] for n in known) - OVERLAP)
    added = store.add(records, "spacetrack")
    result = SyncResult("spacetrack", len(ids), len(records), added)
    store.log_sync("spacetrack", result.satellites, result.fetched, result.added,
                   f"{len(new)} backfilled, {len(known)} updated")
    return result


@dataclass
class ScreenResult:
    objects: int
    primaries: int
    approaches: int
    closest: str
    seconds: float
    missing_groups: list[str]

    def __str__(self) -> str:
        missing = f" (could not load: {', '.join(self.missing_groups)})" if self.missing_groups else ""
        return (f"screened {self.primaries} watched satellites against {self.objects:,} objects in"
                f" {self.seconds:.0f} s: {self.approaches} approaches under {SCREEN_KM:g} km{missing}"
                + (f", closest {self.closest}" if self.closest else ""))


def screening_records(groups: list[str] = SCREEN_GROUPS) -> tuple[list[dict], list[str]]:
    """Every object to screen against, deduplicated by NORAD id, and the groups that failed."""
    by_id, missing = {}, []
    for group in groups:
        try:
            for r in fetch_group(group):
                by_id[int(float(r["NORAD_CAT_ID"]))] = r
        except Exception as e:  # noqa: BLE001  one missing group should not stop the screening
            log.warning("OrbTrack screening: could not load CelesTrak '%s' (%s)", group, e)
            missing.append(group)
    return list(by_id.values()), missing


def screen_watchlist(store_path=None, hours: float = SCREEN_HOURS, km: float = SCREEN_KM,
                     now: datetime | None = None, records: list[dict] | None = None) -> ScreenResult:
    """Screen the watchlist for the coming ``hours`` and store what comes within ``km``.

    Takes a path rather than a store, so the app can run it in a separate process.
    """
    from orbtrack.engine import Catalog
    from orbtrack.engine.conjunctions import screen

    started = time.perf_counter()
    store = HistoryStore(store_path)
    missing: list[str] = []
    if records is None:
        records, missing = screening_records()
    catalog = Catalog(records)
    primaries = [s for n in store.watchlist() if (s := catalog.get(n))]
    now = now or datetime.now(UTC)
    found = screen(primaries, catalog, now, hours=hours, threshold_km=km)
    store.replace_approaches(now, found)
    closest = f"{found[0].miss_km:.2f} km, {found[0].primary_name} × {found[0].secondary_name}" if found else ""
    return ScreenResult(len(catalog), len(primaries), len(found), closest, time.perf_counter() - started, missing)

