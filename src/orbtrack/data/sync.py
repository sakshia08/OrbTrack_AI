"""Filling the history store: CelesTrak snapshots and Space-Track backfill for the watchlist."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from orbtrack.data.history import HistoryStore
from orbtrack.data.spacetrack import SpaceTrack

BACKFILL_DAYS = 365
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
