# OrbTrack AI

Real-time space situational awareness (SSA) and satellite monitoring that runs entirely on your machine: live orbits from free public data, pass predictions, and (soon) a 3D globe, maneuver detection and an optional local LLM.

- Product spec: [docs/PRD.md](docs/PRD.md)
- Build plan and phases: [docs/PLAN.md](docs/PLAN.md)

## Quick start

Needs [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run orbtrack where ISS          # where the ISS is right now
uv run orbtrack passes ISS         # passes over you in the next 48 h (UTC)
uv run orbtrack catalog            # the active catalog by orbit class
uv run pytest                      # offline test suite
```

Set your location with `ORBTRACK_HOME="lat,lon,elevation_m"` (default: Bangalore), or pass `--lat/--lon` to `passes`. Downloads are cached in `~/.cache/orbtrack` (override with `ORBTRACK_CACHE`); CelesTrak data is refreshed at most every 2 hours.

## Layout

| Path | What |
| --- | --- |
| `src/orbtrack/data/celestrak.py` | CelesTrak GP client (OMM JSON), cached on disk |
| `src/orbtrack/engine/catalog.py` | Satellite catalog, name/NORAD lookup, orbit classes |
| `src/orbtrack/engine/propagate.py` | Vectorised SGP4 for the whole catalog, ground tracks |
| `src/orbtrack/engine/passes.py` | Passes over an observer, naked-eye visibility |
| `src/orbtrack/cli.py` | `orbtrack` command |
| `tests/` | Offline tests on a frozen catalog, plus an independent pass cross-check |
| `notebooks/` | The original exploration notebooks, kept as demos |
