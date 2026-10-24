# OrbTrack AI — What each phase added

A plain-language record of what each phase built. The plan for upcoming phases is in [PLAN.md](PLAN.md).

## Phase 1 · Foundation

- **`orbtrack` package** (`src/orbtrack/`):
  - downloads current orbits from CelesTrak and caches them for 2 hours, which is how often CelesTrak updates
  - looks up satellites by name or NORAD id and labels each orbit LEO, MEO, GEO, HEO or Starlink
  - computes positions for the whole active catalog in one fast batch
  - draws ground tracks split at the ±180° longitude line
  - predicts passes over a location and marks whether you can see them with the naked eye
- **`orbtrack` command:** `uv run orbtrack where ISS`, `passes ISS` and `catalog` all work against live data. At the time that was 16,760 satellites, all computed without errors.
- **Tests:** they run offline on a saved copy of the space-station orbits from 2026-10-23. The pass test checks results against a second pass calculator written from scratch in `tests/reference.py`. Every ISS rise and set time matched within 0.2 seconds (the gate was 60 seconds).
- **Notebooks:** moved to `notebooks/` and fixed. They now use the current time instead of a fixed day in 2025, import `numpy`, use the current CelesTrak endpoint, and the Colab links point to the new paths.
- **Project files:** `uv` project (`pyproject.toml`, `uv.lock`), `.gitignore`, and a README with a quick start.
- **Docs:** `docs/PRD.md` (product spec) and `docs/PLAN.md` (six phases).

## Phase 2 · Live globe

- **The app:** `uv run orbtrack app` opens a full-screen 3D globe in your browser. It is still all Python (NiceGUI); the only JavaScript is one small component (`src/orbtrack/app/globe.js`) plus vendored copies of globe.gl and three.js.
- **The Earth:**
  - real day and night: the Sun's position is calculated every minute, the day side is lit, and the night side shows city lights
  - a thin warm line where the Sun is setting, a sun glint on the oceans and a blue glow around the edge
  - all Earth and star images are saved in the repo, so nothing has to be downloaded from the internet when the app runs
- **The swarm:** every active satellite (16,760 at the time) is a glowing dot coloured by orbit type. The server sends positions every 2 seconds and the graphics card moves the dots smoothly in between, so the browser does almost no work per satellite.
- **Follow cam:** click any satellite (or ISS, Tiangong, Hubble, Surprise me) and the camera locks onto it, with:
  - a fading comet trail behind it and a dashed line showing the orbit ahead
  - a card with live altitude, speed, position, orbit period and how old its orbit data is
  - the next pass over your location
  - scroll to zoom, Esc to let go
- **Little things:** hovering over a dot shows its name; clicking an orbit type in the legend hides or shows it; a loading screen covers the page until the first satellites are on screen.
- **Speed:** in testing, the first frame with satellites appeared 1.1 to 1.4 seconds after the page opened (the gate was 3 seconds). That was measured in a headless browser with software graphics, so a real laptop should be faster.
- **Tests:** 11 new tests check the positions sent to the browser against Skyfield, that the comet trail meets the satellite, and the Sun's position. 25 tests in total, all passing.

## Phase 3 · Mission control HUD

- **Time machine** (the bar along the bottom):
  - pause, rewind and fast-forward up to 1000×, or drag along the track to go anywhere from 2 days ago to 2 days ahead; LIVE brings you back
  - the track shows day and night over your city, and coloured marks for upcoming passes (gold ones you can see with your eyes)
  - everything follows the clock: satellites, the day/night line, the comet trail, the card and the next-pass time
  - satellites now fly along their real orbits while the Earth turns underneath, so they stay smooth even at 1000×
- **Command palette:** press Ctrl+K (or `/`) and type. It understands satellite names ("iss", "hubble", "tiangong", or a NORAD number), views ("your sky", "passes tonight"), orbit types ("starlink", "gps"), time ("+6h", "-30m", "tomorrow", "60x", "pause", "live"). No AI needed; it is a simple keyword matcher.
- **Your sky:** a second view from your city looking up.
  - a sky dome with a compass and height rings, blue by day, orange at sunset, clear at night, with the Sun drawn in
  - how many satellites are above your horizon right now
  - the next day's passes of the ISS, Tiangong and Hubble drawn as arcs across the sky, plus whichever satellite you are following
  - click a pass and the clock jumps there, runs at 10× and the camera tracks the satellite across your sky
  - drag to look around, scroll to zoom
- **Little things:** satellites in Earth's shadow dim (the sunlit ones are the ones you could see at night); the comet trail always stays attached to the satellite; quick targets moved into the legend; keyboard shortcuts (Space, ← →, L, V, Esc).
- **Tests:** new tests for the clock, the palette, the inertial keyframes (including one that checks the 1000× interpolation stays within 25 km of the real orbit) and the sky geometry (pass arcs point at the real ISS within 0.2°). 66 tests in total (up from 25), all passing.
