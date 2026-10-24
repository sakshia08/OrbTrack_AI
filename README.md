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

## Layout

| Path | What |
| --- | --- |
| `src/orbtrack/data/celestrak.py` | CelesTrak GP client (OMM JSON), cached on disk |
| `src/orbtrack/engine/catalog.py` | Satellite catalog, name/NORAD lookup, orbit classes |
| `src/orbtrack/engine/propagate.py` | Vectorised SGP4 for the whole catalog, ground tracks |
| `src/orbtrack/engine/passes.py` | Passes over an observer, naked-eye visibility |
| `src/orbtrack/app/main.py` | The web page: globe, HUD, palette, your-sky panel, timers that push data to the browser |
| `src/orbtrack/app/frames.py` | Positions, trails, Sun, passes and sky geometry, packed for the browser |
| `src/orbtrack/app/clock.py` | The time machine: a simulated clock with rates, pause and jumps |
| `src/orbtrack/app/palette.py` | Ctrl+K command palette: keywords to actions, no LLM |
| `src/orbtrack/app/globe.js` | The globe component: Earth shader, satellite point cloud, follow cam, your-sky view |
| `src/orbtrack/app/timebar.js` | The time machine bar: clock, speed buttons, ±48 h scrub track |
| `src/orbtrack/app/static/` | Vendored globe.gl, three.js and Earth/star textures |
| `src/orbtrack/cli.py` | `orbtrack` command |
| `tests/` | Offline tests on a frozen catalog, plus an independent pass cross-check |
| `notebooks/` | The original exploration notebooks, kept as demos |
