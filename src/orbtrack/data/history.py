"""Orbit history: every element set we have ever seen, in one local DuckDB file.

Rows come from CelesTrak snapshots (the watchlist's part of every catalog refresh) and,
when a login is set, Space-Track (``gp_history``, a year of backfill). A satellite + epoch
pair is stored once, so re-downloading the same data is harmless. Maneuvers the detector
finds live alongside, in ``events``.

Connections are opened per call and closed straight away, so the app and the
``orbtrack history`` command can take turns with the same file.
"""

import time
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import numpy as np

from orbtrack.config import DEFAULT_WATCHLIST, data_dir

MU_KM3_S2 = 398600.4418
EARTH_RADIUS_KM = 6378.137

# OMM field -> column. Space-Track sends every value as a string, CelesTrak as numbers.
NUMERIC = {
    "MEAN_MOTION": "mean_motion",  # revolutions per day
    "ECCENTRICITY": "eccentricity",
    "INCLINATION": "inclination",  # degrees
    "RA_OF_ASC_NODE": "raan",
    "ARG_OF_PERICENTER": "arg_perigee",
    "MEAN_ANOMALY": "mean_anomaly",
    "BSTAR": "bstar",
    "MEAN_MOTION_DOT": "mean_motion_dot",
    "MEAN_MOTION_DDOT": "mean_motion_ddot",
}

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS elements (
    norad_id INTEGER NOT NULL,
    epoch TIMESTAMP NOT NULL,
    source VARCHAR NOT NULL,
    name VARCHAR,
    {", ".join(f"{col} DOUBLE" for col in NUMERIC.values())},
    element_set_no INTEGER,
    fetched_at TIMESTAMP NOT NULL,
    PRIMARY KEY (norad_id, epoch)
);
CREATE TABLE IF NOT EXISTS watchlist (norad_id INTEGER PRIMARY KEY, added_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS syncs (
    synced_at TIMESTAMP, source VARCHAR, satellites INTEGER, fetched INTEGER, added INTEGER, note VARCHAR
);
CREATE TABLE IF NOT EXISTS events (
    norad_id INTEGER NOT NULL,
    epoch TIMESTAMP NOT NULL,  -- first element set after the change
    before_epoch TIMESTAMP,
    name VARCHAR, kind VARCHAR, delta_a_km DOUBLE, delta_i_deg DOUBLE, confidence DOUBLE, reason VARCHAR,
    detected_at TIMESTAMP,
    PRIMARY KEY (norad_id, epoch, kind)
);
CREATE OR REPLACE VIEW orbits AS
SELECT *,
    pow({MU_KM3_S2} / pow(mean_motion * 2 * pi() / 86400, 2), 1.0 / 3) AS semimajor_km,
    pow({MU_KM3_S2} / pow(mean_motion * 2 * pi() / 86400, 2), 1.0 / 3) * (1 - eccentricity)
        - {EARTH_RADIUS_KM} AS perigee_km,
    pow({MU_KM3_S2} / pow(mean_motion * 2 * pi() / 86400, 2), 1.0 / 3) * (1 + eccentricity)
        - {EARTH_RADIUS_KM} AS apogee_km
FROM elements;
"""


def default_path() -> Path:
    return data_dir() / "history.duckdb"


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class HistoryStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or default_path())
        with self._connect() as con:
            con.execute(SCHEMA)
            if con.execute("SELECT count(*) FROM watchlist").fetchone()[0] == 0:
                self._watch(con, DEFAULT_WATCHLIST)

    @contextmanager
    def _connect(self, attempts: int = 20):
        """A short-lived connection, waiting politely if another process holds the file."""
        for attempt in range(attempts):
            try:
                con = duckdb.connect(str(self.path))
                break
            except duckdb.IOException:
                if attempt == attempts - 1:
                    raise
                time.sleep(0.25)
        try:
            yield con
        finally:
            con.close()

    # --- Writing ------------------------------------------------------------

    def add(self, records: Iterable[dict], source: str) -> int:
        """Store OMM records; returns how many were new."""
        rows = [r for r in records if r.get("EPOCH") and _number(r.get("NORAD_CAT_ID")) is not None]
        if not rows:
            return 0
        columns = {
            "norad_id": [int(_number(r["NORAD_CAT_ID"])) for r in rows],
            "epoch": [str(r["EPOCH"]) for r in rows],
            "name": [r.get("OBJECT_NAME") for r in rows],
            **{col: [_number(r.get(key)) for r in rows] for key, col in NUMERIC.items()},
            "element_set_no": [int(n) if (n := _number(r.get("ELEMENT_SET_NO"))) is not None else None for r in rows],
        }
        names = list(columns)
        select = ", ".join(
            f"CAST(unnest(${i + 1}) AS TIMESTAMP)" if name == "epoch" else f"unnest(${i + 1})"
            for i, name in enumerate(names)
        )
        now = datetime.now(UTC).replace(tzinfo=None)
        with self._connect() as con:
            before = con.execute("SELECT count(*) FROM elements").fetchone()[0]
            con.execute(
                f"""INSERT OR IGNORE INTO elements ({", ".join(names)}, source, fetched_at)
                    SELECT *, ${len(names) + 1}, ${len(names) + 2} FROM (SELECT {select})""",
                [*columns.values(), source, now],
            )
            return con.execute("SELECT count(*) FROM elements").fetchone()[0] - before

    def log_sync(self, source: str, satellites: int, fetched: int, added: int, note: str = "") -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO syncs VALUES (?, ?, ?, ?, ?, ?)",
                [datetime.now(UTC).replace(tzinfo=None), source, satellites, fetched, added, note],
            )

    def _watch(self, con, norad_ids: Iterable[int]) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        for n in norad_ids:
            con.execute("INSERT OR IGNORE INTO watchlist VALUES (?, ?)", [int(n), now])

    def watch(self, norad_ids: Iterable[int]) -> None:
        with self._connect() as con:
            self._watch(con, norad_ids)

    def unwatch(self, norad_ids: Iterable[int]) -> None:
        with self._connect() as con:
            for n in norad_ids:
                con.execute("DELETE FROM watchlist WHERE norad_id = ?", [int(n)])

    def replace_events(self, norad_id: int, events: list) -> None:
        """Swap in a fresh detection for one satellite (a rerun with more data can drop or add events)."""
        now = datetime.now(UTC).replace(tzinfo=None)
        with self._connect() as con:
            con.execute("DELETE FROM events WHERE norad_id = ?", [int(norad_id)])
            for e in events:
                con.execute(
                    "INSERT OR REPLACE INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [int(norad_id), e.epoch, e.before_epoch, e.name, e.kind, e.delta_a_km, e.delta_i_deg,
                     e.confidence, e.reason, now],
                )

    # --- Reading ------------------------------------------------------------

    def watchlist(self) -> list[int]:
        with self._connect() as con:
            return [r[0] for r in con.execute("SELECT norad_id FROM watchlist ORDER BY added_at, norad_id").fetchall()]

    def latest_epochs(self, norad_ids: Iterable[int], source: str | None = None) -> dict[int, datetime]:
        """The newest epoch held for each satellite (only those with any history)."""
        ids = [int(n) for n in norad_ids]
        if not ids:
            return {}
        sql = "SELECT norad_id, max(epoch) FROM elements WHERE norad_id IN (SELECT unnest(?))"
        params: list = [ids]
        if source:
            sql += " AND source = ?"
            params.append(source)
        with self._connect() as con:
            return dict(con.execute(sql + " GROUP BY norad_id", params).fetchall())

    def series(self, norad_id: int, since: datetime | None = None) -> dict[str, np.ndarray]:
        """One satellite's history in epoch order: epochs (datetime64), elements and derived altitudes."""
        sql = """SELECT epoch, mean_motion, eccentricity, inclination, raan, bstar,
                        semimajor_km, perigee_km, apogee_km, source
                 FROM orbits WHERE norad_id = ?"""
        params: list = [int(norad_id)]
        if since is not None:
            sql += " AND epoch >= ?"
            params.append(since.replace(tzinfo=None))
        with self._connect() as con:
            rows = con.execute(sql + " ORDER BY epoch", params).fetchall()
        names = ["epoch", "mean_motion", "eccentricity", "inclination", "raan", "bstar",
                 "semimajor_km", "perigee_km", "apogee_km", "source"]
        if not rows:
            return {name: np.array([]) for name in names}
        cols = list(zip(*rows, strict=True))
        out = {name: np.array(col, dtype=float) for name, col in zip(names[1:-1], cols[1:-1], strict=True)}
        out["epoch"] = np.array(cols[0], dtype="datetime64[us]")
        out["source"] = np.array(cols[-1])
        return out

    def element_sets(self, norad_id: int, epochs: Iterable[datetime]) -> dict[datetime, dict]:
        """Stored element sets at these epochs, as OMM records SGP4 can be started from."""
        wanted = [e.replace(tzinfo=None) for e in epochs]
        with self._connect() as con:
            cur = con.execute(
                "SELECT * FROM elements WHERE norad_id = ? AND epoch IN (SELECT unnest(?))", [int(norad_id), wanted]
            )
            names = [d[0] for d in cur.description]
            rows = [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
        out = {}
        for row in rows:
            omm = {key: row[col] for key, col in NUMERIC.items()}
            omm.update({
                "OBJECT_NAME": row["name"] or str(row["norad_id"]),
                "NORAD_CAT_ID": row["norad_id"],
                "EPOCH": row["epoch"].strftime("%Y-%m-%dT%H:%M:%S.%f"),
                "ELEMENT_SET_NO": row["element_set_no"] or 999,
            })
            out[row["epoch"]] = omm
        return out

    def names(self, norad_ids: Iterable[int]) -> dict[int, str]:
        """The most recent name each satellite was published under."""
        ids = [int(n) for n in norad_ids]
        with self._connect() as con:
            return dict(con.execute(
                "SELECT norad_id, arg_max(name, epoch) FROM elements WHERE norad_id IN (SELECT unnest(?))"
                " GROUP BY norad_id", [ids]).fetchall())

    def events(self, since: datetime | None = None, norad_id: int | None = None) -> list[dict]:
        """Detected events, newest first."""
        sql, params = "SELECT * FROM events WHERE true", []
        if since is not None:
            sql += " AND epoch >= ?"
            params.append(since.replace(tzinfo=None))
        if norad_id is not None:
            sql += " AND norad_id = ?"
            params.append(int(norad_id))
        with self._connect() as con:
            cur = con.execute(sql + " ORDER BY epoch DESC", params)
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]

    def status(self) -> dict:
        with self._connect() as con:
            by_source = con.execute(
                """SELECT source, count(*), count(DISTINCT norad_id), min(epoch), max(epoch)
                   FROM elements GROUP BY source ORDER BY source"""
            ).fetchall()
            total = con.execute("SELECT count(*), count(DISTINCT norad_id) FROM elements").fetchone()
            last_syncs = con.execute(
                "SELECT source, max(synced_at) FROM syncs GROUP BY source ORDER BY source"
            ).fetchall()
        return {
            "path": str(self.path),
            "rows": total[0],
            "satellites": total[1],
            "by_source": [
                {"source": s, "rows": n, "satellites": k, "first": a, "last": b} for s, n, k, a, b in by_source
            ],
            "last_sync": dict(last_syncs),
            "watchlist": len(self.watchlist()),
        }

    def export_parquet(self, directory: Path) -> Path:
        """Write the whole history as Parquet, one file per year of epoch."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute(
                f"""COPY (SELECT *, year(epoch) AS year FROM orbits)
                    TO '{directory.as_posix()}' (FORMAT parquet, PARTITION_BY (year), OVERWRITE_OR_IGNORE)"""
            )
        return directory
