"""Background jobs inside the app: keep the live catalog fresh and the orbit history growing.

- every 2 h: download the CelesTrak catalog again; open pages pick it up without a reload,
  and the watchlist's orbits go into the history store
- every night (and shortly after start if it has not run for a day): fetch new Space-Track
  history for the watchlist, when a Space-Track login is configured
- after every snapshot and sync: look for maneuvers in the watchlist's history (13 satellites
  take well under a second; the first run trains the model, about 5 s)
"""

import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nicegui import run

from orbtrack.config import spacetrack_credentials
from orbtrack.data.celestrak import fetch_group
from orbtrack.data.history import HistoryStore
from orbtrack.data.spacetrack import SpaceTrackError
from orbtrack.data.sync import snapshot, sync_spacetrack
from orbtrack.detect.model import detect_watchlist, load_or_train
from orbtrack.engine import Catalog

log = logging.getLogger("orbtrack")

CATALOG_EVERY = timedelta(hours=2, minutes=5)  # just over CelesTrak's update interval
NIGHTLY_HOUR = 3  # local time

# The live catalog shared by every page. `version` goes up on each refresh so pages can notice.
state: dict = {"catalog": None, "records": None, "group": "active", "version": 0, "store": None,
                "detector": None}


def load_catalog() -> None:
    records = fetch_group(state["group"])
    ts = state["catalog"].ts if state["catalog"] else None
    state["catalog"] = Catalog(records, ts)
    state["records"] = records
    state["version"] += 1
    log.warning("OrbTrack: %d satellites loaded from CelesTrak '%s'", len(state["catalog"]), state["group"])


def store() -> HistoryStore:
    if state["store"] is None:
        state["store"] = HistoryStore()
    return state["store"]


def find_events() -> list:
    if state["detector"] is None:
        state["detector"] = load_or_train()
    return detect_watchlist(store(), state["detector"])


async def detect_events() -> None:
    events = await run.io_bound(find_events)
    if events is not None:
        log.warning("OrbTrack events: %d maneuvers and decays in the watchlist's history", len(events))


async def save_snapshot() -> None:
    result = await run.io_bound(snapshot, store(), state["records"])
    if result is not None:
        log.warning("OrbTrack history: %s", result)
    await detect_events()


async def refresh_catalog() -> None:
    try:
        await run.io_bound(load_catalog)
    except Exception as e:  # keep serving the old catalog if CelesTrak is unreachable
        log.warning("OrbTrack: catalog refresh failed, keeping the old one (%s)", e)
        return
    await save_snapshot()


async def nightly_sync() -> None:
    credentials = spacetrack_credentials()
    if credentials is None:
        log.warning("OrbTrack history: no Space-Track login in .env, skipping the Space-Track sync")
        return
    try:
        result = await run.io_bound(sync_spacetrack, store(), credentials)
    except (SpaceTrackError, OSError) as e:
        log.warning("OrbTrack history: Space-Track sync failed (%s)", e)
        return
    if result is not None:
        log.warning("OrbTrack history: %s", result)
    await detect_events()


def spacetrack_overdue() -> bool:
    last = store().status()["last_sync"].get("spacetrack")
    return last is None or datetime.now(UTC).replace(tzinfo=None) - last > timedelta(hours=24)


async def start() -> None:
    """Called once the app is up: save a snapshot of the catalog and start the schedule."""
    scheduler = AsyncIOScheduler()
    now = datetime.now()
    scheduler.add_job(refresh_catalog, "interval", seconds=CATALOG_EVERY.total_seconds(),
                      next_run_time=now + CATALOG_EVERY)
    scheduler.add_job(nightly_sync, "cron", hour=NIGHTLY_HOUR, minute=17)
    scheduler.add_job(save_snapshot, "date", run_date=now + timedelta(seconds=20))
    if spacetrack_credentials() and spacetrack_overdue():
        scheduler.add_job(nightly_sync, "date", run_date=now + timedelta(seconds=45))
    scheduler.start()
    state["scheduler"] = scheduler
