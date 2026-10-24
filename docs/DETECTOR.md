# Maneuver detector

OrbTrack looks through each watched satellite's orbit history for **maneuvers**: orbit raises, orbit lowers and plane changes. It also flags satellites whose perigee has dropped low enough to re-enter soon (**decay**). It runs on the laptop in well under a second for the watchlist, with no LLM.

## How it works

1. **One step at a time.** For each pair of consecutive element sets, OrbTrack measures how much the mean semimajor axis, inclination and eccentricity changed (`src/orbtrack/detect/features.py`).
2. **Against the satellite's own normal.** Drag and natural drift are removed using the median rate of the 24 neighbouring steps. The step that is left is divided by the satellite's usual scatter, which is measured robustly from those same neighbours. The result is a z-score that means the same for the ISS as for a geostationary satellite.
3. **Did it stick?** A burn leaves the orbit at a new level. A bad fit is a spike that snaps back on the next element set. OrbTrack compares the level of the three sets after a step with the three before it, and checks whether the next step goes the opposite way.
4. **A model weighs it all.** Gradient-boosted trees (scikit-learn `HistGradientBoostingClassifier`) turn the 11 features into a probability. Flagged steps next to each other count as one event, since one burn is sometimes caught across two fits.
5. **A reason you can read.** For example: "mean altitude rose 1.46 km between 31 Jan 00:00 and 31 Jan 06:00 UTC, 38× its usual scatter".

## What it learns from

Public maneuver labels are rare, and Space-Track (the only free source of past orbits) is not switched on yet. So the detector trains on **simulated histories** (`src/orbtrack/detect/synthetic.py`) built to look like real TLE streams:

| Profile | Like | Element sets | Scatter in mean altitude | Maneuvers |
| --- | --- | --- | --- | --- |
| station | ISS, Tiangong | every 3–8 h | 15–50 m | reboosts of 0.5–3 km, about one a month, some lowerings |
| leo | Landsat, NOAA 20, CryoSat | every 6–20 h | 10–40 m | trims of 0.1–1.2 km, inclination tweaks of 0.004–0.04° |
| geo | GOES, Meteosat, Himawari | every 10–30 h | 10–50 m | east-west station-keeping of 0.25–2 km every couple of weeks, north-south burns of 0.015–0.1° |

The simulated histories also include the things that fool a simple threshold:
- drag that rises and falls with the Sun's 27-day rotation
- 3% bad fits, with 4–10× the usual scatter
- gaps of two to four days
- burns that the first fit afterwards only partly catches

The model trains on one fleet (450 satellites × 120 days), picks its alert threshold on a second fleet, and is graded on a third fleet that it never sees during training.

## Results

From `orbtrack events train`:

```
Test fleet: 300 simulated satellites × 120 days (seed 3, never seen in training), threshold 0.450
  overall    precision 96%  recall 93%  (1379 burns, 1333 flagged)
  geo        precision 99%  recall 94%  (693 burns, 656 flagged)
  leo        precision 93%  recall 81%  (278 burns, 243 flagged)
  station    precision 93%  recall 99%  (408 burns, 434 flagged)
  recall by kind: lower 93%, plane 96%, raise 93%
  plain rule (any step over 6× the usual scatter): precision 56%  recall 86%  (1379 burns, 2132 flagged)
```

- **Phase 4b gate (recall ≥ 80% at precision ≥ 70%): met** on the simulated test fleet.
- **Most misses are LEO trims smaller than about 3× the scatter.** They are hard to separate from noise in the elements alone.
- **False alarms on quiet ISS-like history:** about one every 400 days.
- **Against a plain rule:** the model keeps recall higher while cutting false alarms by more than five times.

## Honest limits

- **Scored on simulated histories.** These numbers come from data shaped like real TLEs, not from labelled real events. Real histories will have quirks the simulator lacks, such as operator-specific fitting and element sets re-issued with corrections. Expect real-world numbers to be lower until the detector is checked against real ISS reboosts. That check needs past orbits, so it waits for a Space-Track login or for enough CelesTrak snapshots to build up.
- **Needs history first.** A satellite needs at least 8 element sets before anything is judged. With CelesTrak snapshots alone, that takes a few days for the ISS and a week or more for GEO satellites.
- **Not for operational use.** TLEs are not accurate enough for real maneuver decisions.

## Commands

```bash
uv run orbtrack events            # events in the last 30 days (also: events ISS, --days 90)
uv run orbtrack events detect     # run the detector over the watchlist now
uv run orbtrack events train      # retrain (about 5 s) and print the report above
uv run orbtrack events evaluate   # grade the saved model again
```

The app runs the detector after every snapshot and every sync. The trained model is saved in `~/.local/share/orbtrack/models/` so it trains only once.
