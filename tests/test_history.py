"""History store, Space-Track client and sync, all offline (fake HTTP, temporary DuckDB files)."""

from datetime import UTC, datetime, timedelta

import httpx
import numpy as np
import pytest

from orbtrack.config import DEFAULT_WATCHLIST, read_env_file, spacetrack_credentials
from orbtrack.data import spacetrack
from orbtrack.data.history import HistoryStore
from orbtrack.data.spacetrack import SpaceTrack, SpaceTrackError
from orbtrack.data.sync import sync_spacetrack


def iss_history(stations_records, days: int, start: datetime, raise_on: int | None = None) -> list[dict]:
    """A fake Space-Track gp_history for the ISS: four element sets a day, all values as strings,
    slowly decaying, with one 1.5 km reboost on day ``raise_on``."""
    base = next(r for r in stations_records if r["NORAD_CAT_ID"] == 25544)
    out = []
    for k in range(days * 4):
        epoch = start + timedelta(hours=6 * k)
        n = base["MEAN_MOTION"] + 0.0002 * k / 4  # mean motion creeps up as it sinks
        if raise_on is not None and k >= raise_on * 4:
            n -= 0.0045  # about 1.5 km higher
        out.append({**{key: str(v) for key, v in base.items()}, "EPOCH": epoch.strftime("%Y-%m-%dT%H:%M:%S.%f"),
                    "MEAN_MOTION": f"{n:.8f}", "ELEMENT_SET_NO": str(900 + k)})
    return out


@pytest.fixture
def store(tmp_path):
    return HistoryStore(tmp_path / "history.duckdb")


# --- Config ---------------------------------------------------------------------


def test_env_file_parsing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# login\nSPACETRACK_IDENTITY = me@example.com\nSPACETRACK_PASSWORD='p=ss word'\n\nJUNK\n")
    assert read_env_file(env) == {"SPACETRACK_IDENTITY": "me@example.com", "SPACETRACK_PASSWORD": "p=ss word"}
    assert read_env_file(tmp_path / "missing") == {}


def test_credentials_from_file_and_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("SPACETRACK_IDENTITY", raising=False)
    monkeypatch.delenv("SPACETRACK_PASSWORD", raising=False)
    env = tmp_path / ".env"
    assert spacetrack_credentials(env) is None
    env.write_text("SPACETRACK_IDENTITY=a\nSPACETRACK_PASSWORD=b\n")
    assert spacetrack_credentials(env) == ("a", "b")
    monkeypatch.setenv("SPACETRACK_PASSWORD", "from-env")
    assert spacetrack_credentials(env) == ("a", "from-env")  # the environment wins


# --- Store ----------------------------------------------------------------------


def test_adding_is_idempotent_and_accepts_both_formats(store, stations_records):
    assert store.add(stations_records, "celestrak") == len(stations_records)  # numbers (CelesTrak)
    assert store.add(stations_records, "celestrak") == 0
    as_strings = [{k: str(v) for k, v in r.items()} for r in stations_records]  # strings (Space-Track)
    assert store.add(as_strings, "spacetrack") == 0  # same satellite + epoch is the same row
    assert store.status()["rows"] == len(stations_records)


def test_series_derives_altitudes_in_epoch_order(store, stations_records):
    start = datetime(2026, 1, 1)
    records = iss_history(stations_records, days=10, start=start, raise_on=5)
    store.add(list(reversed(records)), "spacetrack")
    s = store.series(25544)
    assert len(s["epoch"]) == 40 and np.all(np.diff(s["epoch"]) > np.timedelta64(0, "s"))
    assert np.all((s["perigee_km"] > 380) & (s["perigee_km"] < s["apogee_km"]) & (s["apogee_km"] < 460))
    mean_alt = s["semimajor_km"] - 6378.137
    jumps = np.diff(mean_alt)
    assert jumps.max() == pytest.approx(1.5, abs=0.2) and np.argmax(jumps) == 19  # the reboost
    assert len(store.series(25544, since=start + timedelta(days=8))["epoch"]) == 8


def test_latest_epochs_by_source(store, stations_records):
    store.add(iss_history(stations_records, 3, datetime(2026, 1, 1)), "spacetrack")
    store.add(stations_records, "celestrak")
    assert store.latest_epochs([25544], source="spacetrack")[25544] == datetime(2026, 1, 3, 18)
    assert store.latest_epochs([25544])[25544] > datetime(2026, 10, 1)  # the CelesTrak copy is newer
    assert store.latest_epochs([99999]) == {}


def test_watchlist_starts_with_defaults(store):
    assert set(store.watchlist()) == set(DEFAULT_WATCHLIST)
    store.watch([12345])
    store.unwatch([25544])
    assert 12345 in store.watchlist() and 25544 not in store.watchlist()


def test_parquet_export(store, stations_records, tmp_path):
    store.add(stations_records, "celestrak")
    out = store.export_parquet(tmp_path / "pq")
    files = list(out.rglob("*.parquet"))
    assert files and all("year=" in f.parent.name for f in files)


# --- Space-Track client ---------------------------------------------------------


def fake_spacetrack(handler) -> SpaceTrack:
    client = httpx.Client(base_url=spacetrack.BASE_URL, transport=httpx.MockTransport(handler))
    return SpaceTrack("me", "secret", client=client, sleep=lambda s: None)


def test_failed_login_raises():
    st = fake_spacetrack(lambda req: httpx.Response(200, json={"Login": "Failed"}))
    with pytest.raises(SpaceTrackError, match="login failed"):
        st.__enter__()


def test_gp_history_batches_ids_and_asks_from_a_date():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ajaxauth/login":
            assert b"identity=me" in request.content
            return httpx.Response(200, text="")
        seen.append(request.url.raw_path.decode())
        return httpx.Response(200, json=[{"NORAD_CAT_ID": "1"}])

    with fake_spacetrack(handler) as st:
        records = st.gp_history(list(range(1, 151)), since=datetime(2025, 10, 24, 13, 5))
    queries = [p for p in seen if "gp_history" in p]
    assert len(queries) == 2 and len(records) == 2  # 150 ids -> batches of 100 and 50
    assert "/NORAD_CAT_ID/1,2,3," in queries[0] and ",150/" in queries[1]
    assert "/EPOCH/%3E2025-10-24/" in queries[0] and queries[0].endswith("/format/json")


def test_throttle_waits_once_the_minute_is_full():
    now = {"t": 0.0}
    slept = []

    def sleep(s):
        slept.append(s)
        now["t"] += s

    st = SpaceTrack("a", "b", client=httpx.Client(), sleep=sleep, clock=lambda: now["t"])
    for _ in range(spacetrack.PER_MINUTE + 3):
        st._throttle()
        now["t"] += 0.1
    assert len(slept) == 1 and slept[0] == pytest.approx(60 - spacetrack.PER_MINUTE * 0.1, abs=0.01)


# --- Sync -----------------------------------------------------------------------


class FakeSpaceTrack:
    """Serves a fixed ISS history and records what it was asked for."""

    calls: list = []
    history: list = []

    def __init__(self, identity, password):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def gp_history(self, norad_ids, since, until=None):
        FakeSpaceTrack.calls.append((tuple(norad_ids), since))
        day = since.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
        return [r for r in self.history if int(r["NORAD_CAT_ID"]) in norad_ids and r["EPOCH"] >= day.isoformat()]


def test_first_sync_backfills_then_only_fetches_new(store, stations_records):
    now = datetime(2026, 1, 31, tzinfo=UTC)
    FakeSpaceTrack.history = iss_history(stations_records, 30, datetime(2026, 1, 1))
    FakeSpaceTrack.calls = []
    store.unwatch(store.watchlist())
    store.watch([25544])

    first = sync_spacetrack(store, ("a", "b"), days=365, now=now, client_factory=FakeSpaceTrack)
    assert (first.fetched, first.added) == (120, 120)
    assert FakeSpaceTrack.calls[-1] == ((25544,), now - timedelta(days=365))

    FakeSpaceTrack.history += iss_history(stations_records, 2, datetime(2026, 1, 31))
    second = sync_spacetrack(store, ("a", "b"), now=now + timedelta(days=2), client_factory=FakeSpaceTrack)
    assert second.added == 8  # two new days, four element sets each
    assert second.fetched < 20  # it asked from the last epoch it had, minus a day of overlap
    assert store.status()["last_sync"]["spacetrack"]


def test_snapshots_keep_only_the_watchlist(store, stations_records):
    from orbtrack.data.sync import snapshot

    store.unwatch(store.watchlist())
    store.watch([25544, 48274])
    result = snapshot(store, stations_records)
    assert (result.satellites, result.added) == (2, 2)
    assert store.status()["satellites"] == 2
