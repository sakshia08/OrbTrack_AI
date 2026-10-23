# OrbTrack AI — Build Plan

_Last updated 2026-10-23. Product spec: [PRD.md](PRD.md)._

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
