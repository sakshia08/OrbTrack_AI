# OrbTrack AI

Real-time space situational awareness (SSA) and satellite monitoring that runs entirely on your machine: a live 3D globe of every active satellite, pass predictions, and (soon) maneuver detection and an optional local LLM.

- Product spec: [docs/PRD.md](docs/PRD.md)
- Build plan and phases: [docs/PLAN.md](docs/PLAN.md)
- What each phase added: [docs/CHANGELOG.md](docs/CHANGELOG.md)

## Quick start

Needs [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run orbtrack app            # the live 3D globe at http://localhost:8080
```

In the app: **Ctrl+K** (or `/`) search and commands · **Space** pause · **← →** slower / faster ·
**L** back to live · **V** globe / your sky · **Esc** let go.

```bash
uv run orbtrack where ISS          # where the ISS is right now
uv run orbtrack passes ISS         # passes over you in the next 48 h (UTC)
uv run orbtrack catalog            # the active catalog by orbit class
uv run pytest                      # offline test suite
```

Set your location with `ORBTRACK_HOME="lat,lon,elevation_m"` (default: Bangalore), or pass `--lat/--lon` to `passes`. Downloads are cached in `~/.cache/orbtrack` (override with `ORBTRACK_CACHE`); CelesTrak data is refreshed at most every 2 hours.

### Orbit history

OrbTrack keeps every orbit it sees in a local DuckDB file (`~/.local/share/orbtrack/history.duckdb`, override with `ORBTRACK_DATA`). For the satellites on the watchlist, the app saves CelesTrak's latest orbits every 2 hours, so history keeps growing (only a few MB a year). CelesTrak only knows each satellite's current orbit, so with a free [Space-Track.org](https://www.space-track.org) account OrbTrack also downloads a year of past orbits for the watchlist, then tops it up every night. The maneuver detector needs that past before it can find anything.

Put your login in a `.env` file in the repo folder (it is git-ignored):

```
SPACETRACK_IDENTITY=you@example.com
SPACETRACK_PASSWORD=your-password
```

```bash
uv run orbtrack history sync           # CelesTrak snapshot + Space-Track (a year the first time, then only what is new)
uv run orbtrack history status         # what is stored
uv run orbtrack history show ISS       # element sets for the last 30 days, with reboosts marked
uv run orbtrack history watch NOAA 20  # add a satellite to the Space-Track watchlist
uv run orbtrack history export out/    # Parquet files, one folder per year
```

### Maneuver detector

After every snapshot the app looks through the watchlist's history for orbit raises, lowers, plane changes and low perigees, and stores them as events. It is a scikit-learn model trained on simulated TLE histories with known burns: 96% precision and 93% recall on satellites it never saw in training. See [docs/DETECTOR.md](docs/DETECTOR.md) for how it works and its limits.

```bash
uv run orbtrack events            # maneuvers in the last 30 days (events ISS, --days 90)
uv run orbtrack events detect     # run it over the watchlist now
uv run orbtrack events train      # retrain (about 5 s) and print the evaluation report
```

In the app, events show as pulsing beacons on the globe, marks on the time machine bar and a list under the legend. Click one (or type "maneuvers this week" or "events ISS" in Ctrl+K) to replay it: the orbit before the event and after it, rebuilt from the stored element sets, with a ghost satellite flying into the burn. A 1.5 km reboost is invisible at globe scale, so the gap between the two orbits is drawn larger, and the replay says by how much. Until the detector has found something, "Show demo events" plays a few made-up but realistic ones, marked DEMO everywhere.

## Layout

| Path | What |
| --- | --- |
| `src/orbtrack/data/celestrak.py` | CelesTrak GP client (OMM JSON), cached on disk |
| `src/orbtrack/data/spacetrack.py` | Space-Track client (`gp_history`, `decay`), rate-limited |
| `src/orbtrack/data/history.py` | DuckDB orbit history store, Parquet export |
| `src/orbtrack/data/sync.py` | CelesTrak snapshots and Space-Track backfill into the store |
| `src/orbtrack/detect/features.py` | Per-step features: robust z-scores against each satellite's own normal |
| `src/orbtrack/detect/synthetic.py` | Simulated TLE histories with labelled burns, for training and grading |
| `src/orbtrack/detect/model.py` | The detector (gradient-boosted trees), threshold choice, evaluation report |
| `src/orbtrack/engine/catalog.py` | Satellite catalog, name/NORAD lookup, orbit classes |
| `src/orbtrack/engine/propagate.py` | Vectorised SGP4 for the whole catalog, ground tracks |
| `src/orbtrack/engine/passes.py` | Passes over an observer, naked-eye visibility |
| `src/orbtrack/app/main.py` | The web page: globe, HUD, palette, your-sky panel, timers that push data to the browser |
| `src/orbtrack/app/frames.py` | Positions, trails, Sun, passes and sky geometry, packed for the browser |
| `src/orbtrack/app/jobs.py` | Background jobs (APScheduler): catalog refresh, history snapshots, nightly sync, detection |
| `src/orbtrack/app/events.py` | Events for the page: where they happened, before/after replays, demo events |
| `src/orbtrack/app/sparkline.py` | Altitude-history sparkline for the follow card |
| `src/orbtrack/app/clock.py` | The time machine: a simulated clock with rates, pause and jumps |
| `src/orbtrack/app/palette.py` | Ctrl+K command palette: keywords to actions, no LLM |
| `src/orbtrack/app/globe.js` | The globe component: Earth shader, satellite point cloud, follow cam, your-sky view |
| `src/orbtrack/app/timebar.js` | The time machine bar: clock, speed buttons, ±48 h scrub track |
| `src/orbtrack/app/static/` | Vendored globe.gl, three.js and Earth/star textures |
| `src/orbtrack/cli.py` | `orbtrack` command |
| `tests/` | Offline tests on a frozen catalog, plus an independent pass cross-check |
| `notebooks/` | The original exploration notebooks, kept as demos |
