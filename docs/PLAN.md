# OrbTrack AI — Build Plan

_Last updated 2026-10-23. Product spec: [PRD.md](PRD.md). What each finished phase added: [CHANGELOG.md](CHANGELOG.md)._

Six phases, no deadlines. Each phase ends in something you can demo and a gate it must pass; when the gate passes, the phase is committed and pushed before the next one starts.

| Phase | Build | Gate |
| --- | --- | --- |
| 1 · Foundation | `orbtrack` package (catalog, propagation, passes), CelesTrak GP client with caching, `uv` project, `pytest`, notebooks fixed and kept as demos | Tests pass; ISS passes match an independent check within 60 s |
| 2 · Live globe | NiceGUI app shell, globe.gl element, full active catalog animated live, night lights + terminator, click-to-follow cam with comet trail | Full catalog animating smoothly; first frame in under 3 s |
| 3 · Mission control HUD | Time machine (scrub, pause, 1000×), glass HUD telemetry, Ctrl+K command palette, "your sky" dome, pass arcs | A friend says "whoa", unprompted |
| 4 · History & detection | Space-Track TLE history into DuckDB + Parquet, APScheduler jobs, scikit-learn maneuver/decay detector, event pulses on the globe | Recall ≥ 80% at precision ≥ 70% on a labelled set |
| 5 · Conjunction watch & space weather | 72 h watchlist screening ranked by miss distance, replay on the globe with TLE-age caveats, NOAA Kp / F10.7 context | Known close approaches reproduced; replay works end to end |
| 6 · Local AI (optional) | Qwen 4B via Ollama, 6 tools with JSON schemas, templated numbers, keyword fallback, 50-question eval, daily brief | ≥ 90% right tool and arguments on the eval; every feature still works with Ollama off |

The order follows the fun: the globe and HUD come first because they are the reward, the ML detector next because it is the real AI, and the local LLM last because everything works without it.

## Phase 1 · Foundation

- [x] Package: `orbtrack/engine` (catalog, propagate, passes), `orbtrack/data` (CelesTrak client with caching), notebooks moved to `notebooks/` as demos
- [x] Notebooks: hardcoded `ts.utc(2025, 6, 16, …)` replaced with "now + N minutes", `numpy` imported, `celestrak.org` GP endpoint
- [x] `pyproject.toml` managed with `uv`; `pytest` suite that runs offline from a frozen TLE fixture
- [x] ISS pass test for one city, cross-checked against an independent brute-force elevation search
- [ ] Free Space-Track.org account created, so history downloads are ready by Phase 4

## Phase 2 · Live globe

- [x] NiceGUI app shell (`orbtrack app`) with a glass HUD: clock, orbit legend, quick targets
- [x] globe.gl element with vendored three.js and Earth textures (works offline)
- [x] Full active catalog as one GPU point cloud, interpolated between server keyframes
- [x] Real-time day/night terminator with city lights, ocean glint and atmosphere
- [x] Click-to-follow camera with comet trail, orbit-ahead line, live telemetry and next pass
- [x] First frame with satellites in under 3 s (1.1–1.4 s measured headless)
- [x] Smooth animation of the full catalog checked on your own laptop

## Phase 3 · Mission control HUD

- [x] Time machine: pause, rewind and fast-forward up to 1000×, scrub ±48 h, back to live; every layer follows the clock
- [x] Satellites move in inertial space and the Earth turns under them, so orbits stay round even at 1000×
- [x] Timeline shows day and night over your city and upcoming passes
- [x] Ctrl+K command palette with a keyword parser (satellites, views, orbit filters, time jumps, speeds); no LLM needed
- [x] "Your sky": camera at your city looking up, sky dome with compass, day/twilight/night sky, pass arcs; click a pass to watch it
- [x] Satellites in Earth's shadow dim; comet trail stays glued to the satellite at any speed
- [ ] A friend says "whoa", unprompted

## Phase 4 · History & detection

Split into three parts, each committed and pushed on its own.

### 4a · History store

- [x] Space-Track client: login, `gp_history` and `decay` queries, polite rate limiting, credentials from a git-ignored `.env`
- [x] DuckDB history store (one file, deduplicated by satellite + epoch) with Parquet export
- [x] CelesTrak snapshots of the watchlist saved every time the catalog refreshes, so history grows even without Space-Track
- [x] APScheduler jobs inside the app: catalog refresh every 2 h (live, no restart) and a nightly Space-Track sync
- [x] `orbtrack history sync | status | show <sat>` commands
- [x] Altitude-history sparkline on the follow card
- [x] Gate: history store fills from CelesTrak snapshots, a second sync adds nothing new, tests run offline
- [ ] Later (optional): connect a Space-Track login and backfill a year of watchlist history

### 4b · Detector

- [x] Features from consecutive element sets (changes in semimajor axis, inclination, eccentricity), per-satellite baselines
- [x] Labelled set: simulated TLE histories with ISS-style reboosts, LEO trims and GEO station-keeping (Space-Track is off, so there are no real past orbits yet)
- [x] scikit-learn model with an evaluation report ([DETECTOR.md](DETECTOR.md)); detection after every snapshot and sync writes events to DuckDB
- [x] `orbtrack events` command
- [x] Gate: recall ≥ 80% at precision ≥ 70% on the labelled set (93% recall, 96% precision on an unseen simulated fleet)
- [ ] Later: check against real ISS reboosts once there is past history (Space-Track login, or a few months of snapshots)

### 4c · Events on the globe

- [ ] Pulsing rings where maneuvers and decays happened
- [ ] Events list and timeline marks; click one to replay it with the orbit before and after
- [ ] Palette: "maneuvers this week", "events ISS"
- [ ] Gate: click an ISS reboost and watch it replay
