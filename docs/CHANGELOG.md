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

## Phase 4a · History store

- **Orbit history on your machine:** every orbit OrbTrack sees is saved in one local DuckDB file (`~/.local/share/orbtrack/history.duckdb`). Each satellite + time is stored once, so downloading the same data twice does no harm. It can be exported to Parquet.
- **Two sources:**
  - CelesTrak: every 2 hours the app saves the latest orbits of the watchlist satellites, so history builds up on its own (only a few MB a year)
  - Space-Track: CelesTrak has no past orbits, so with your free login in a `.env` file OrbTrack downloads a year of history for a watchlist of 13 satellites (ISS, Tiangong, Hubble, GOES and other weather satellites, Landsats), then only what is new each night. Requests are spaced out to stay well inside Space-Track's limits.
- **Space-Track is ready but not switched on yet:** the client is built and tested against a fake server; it starts working as soon as a login is added to `.env`. Until then history grows from CelesTrak only.
- **Runs by itself:** a scheduler inside the app refreshes the catalog every 2 hours without a page reload, and syncs Space-Track every night at 3am (or soon after start if it has not run for a day).
- **New commands:** `orbtrack history sync`, `status`, `show ISS` (reboosts marked ▲), `watch <satellite>`, `export <folder>`.
- **On the follow card:** a glowing chart of the satellite's altitude over the last 90 days, where you can spot reboosts as steps up.
- **Tests:** 14 new offline tests with a fake Space-Track server and temporary databases: duplicates ignored, snapshots keep only the watchlist, a reboost shows as a 1.5 km step, the rate limiter waits when the minute is full, and a second sync only asks for what is new. 80 tests in total, all passing.

## Phase 4b · Detector

- **Finds maneuvers by itself:** after every snapshot and sync, OrbTrack checks each watched satellite's history for orbit raises, orbit lowers, plane changes and low perigees (re-entry soon). It saves each one as an event with a time, a size, a confidence and a reason you can read, for example "mean altitude rose 1.46 km between 31 Jan 00:00 and 31 Jan 06:00 UTC, 38× its usual scatter".
- **Judges each satellite against its own normal:** drag and natural drift are removed and every change is measured in units of that satellite's usual scatter, so one model works for the ISS, sun-synchronous satellites and geostationary ones. It also checks whether a change stuck (a burn) or snapped back (a bad fit).
- **Learns without Space-Track:** there are no past orbits yet, so it trains on simulated TLE histories built to look like the real thing, with uneven update times, scatter, changing drag, bad fits, long gaps and burns caught across two fits. Training takes about 5 seconds and runs only once.
- **Measured:** on 300 simulated satellites it never saw in training, it found 93% of burns, and 96% of what it flagged was real. That passes the gate (80% / 70%). A plain threshold on the same data gets 56% precision. Real-world numbers will be lower until it is checked on real ISS reboosts. docs/DETECTOR.md explains it all.
- **New commands:** `orbtrack events` (also `events ISS`, `--days 90`), `events detect`, `events train`, `events evaluate`.
- **Tests:** 12 new offline tests: a step that sticks versus a spike that snaps back, steady decay is not an event, raise, lower and plane change named correctly, rare false alarms on quiet history, the gate on an unseen fleet, the saved model reloads, and events are stored and replaced on rerun. 92 tests in total, all passing.

## Phase 4c · Events on the globe

- **Events you can see:** every maneuver the detector finds shows up in three places:
  - as a glowing beacon with rings spreading out on the globe, at the spot on Earth the satellite was over
  - as a diamond on the time machine bar
  - in a new events panel under the legend, with a 30-day strip and a list (satellite, kind, size, how long ago)
- **Replay:** click an event to watch it happen.
  - The camera flies to the orbit, and the orbit before (dashed) and after (glowing) are rebuilt from the stored element sets, frozen against the Earth as it was at that moment.
  - A ghost satellite flies the old orbit into the burn, flashes, and carries on along the new one as the two orbits part.
  - A banner says what happened, and Esc closes it.
- **Honest scale:** a 1.6 km ISS reboost is invisible on a globe this size, so the gap between the two orbits is drawn larger, and the banner says by how much ("GAP DRAWN ×100").
  - Low orbits are shown from the side, where you can see them separate across the Earth's face.
  - Geostationary ones are shown from above, as a big ring around a small Earth.
- **Command palette:** "maneuvers this week" lists the week's events, and "events ISS" (or any satellite) lists that satellite's events. Pick one to replay it.
- **Demo events until there is history:** the detector needs days of history before it can find anything. Meanwhile, "Show demo events" plays five made-up but realistic events on real watchlist satellites: an ISS reboost, GOES 18 station-keeping, a Landsat 9 plane trim, a Tiangong reboost and a NOAA 20 drag make-up. They are marked DEMO on the panel, the list, the palette and the banner, and are never saved.
- **On phones:** the events list opens as a sheet from the palette, and the replay banner sits above the time bar.
- **Fixed along the way:** labels in 3D now account for the camera's field of view, so they are the same size in the globe and sky views.
- **Tests:**
  - 12 new tests: stored element sets rebuild the exact orbit, a demo burn keeps the satellite in place and raises the far side by twice the change, a plane change tilts the orbit at the node, replays meet at the burn and part most half an orbit later, demo events are marked, and the palette lists the right events.
  - One of them caught a real bug: comparing orbits at the same time breaks when two element sets disagree on where the satellite is along its orbit, so replays now compare them at the same angle.
  - 104 tests in total, all passing.
- **Screenshots** of the events panel, the replays, the palette and the phone layout are in `docs/screenshots/phase-4/`, with a short guide.
