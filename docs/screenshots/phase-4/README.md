# Phase 4 screenshots

Taken on 24 Oct 2026 in headless Chromium (software WebGL) at 1440×900, and 390×844 for the phone. The events are the **demo** set, since the detector had no real history yet.

| | Screenshot | What it shows |
| --- | --- | --- |
| 1 | [Events panel, empty](01-events-panel-empty.png) | What a fresh install shows: no history yet, with a "Show demo events" button |
| 2 | [Events panel, demo](02-events-panel-demo.png) | Five demo events, the DEMO badge, the 30-day strip and the ISS diamond on the time bar |
| 3 | [Event beacons](03-event-beacons.png) | Glowing beacons with spreading rings where the events happened |
| 4 | [ISS reboost replay](04-replay-iss-reboost.png) | Side view of a low orbit: dashed before, glowing after, gap drawn ×100 |
| 5 | [GOES 18 station-keeping replay](05-replay-goes18-station-keeping.png) | A geostationary orbit from above, gap drawn ×1,000 |
| 6 | [Landsat 9 plane change replay](06-replay-landsat9-plane-change.png) | Two orbits tilted apart, burn at the equator crossing |
| 7 | [Palette: "maneuvers this week"](07-palette-maneuvers-this-week.png) | The week's events, ready to replay |
| 8 | [Palette: "events tiangong"](08-palette-events-tiangong.png) | One satellite's events |
| 9 | [Your sky](09-sky-view-unchanged.png) | Sky view: beacons are hidden and labels are the same size as in Phase 3 |
| 10 | [Phone events sheet](10-phone-events-sheet.png) | The events list as a bottom sheet on a phone |

The replay is animated (a ghost satellite flies into the burn and along the new orbit), and a still frame doesn't capture it. To see it live, run `uv run orbtrack app`, click "Show demo events", then click an event.
