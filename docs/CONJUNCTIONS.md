# Close-approach screening

OrbTrack checks every satellite on the watchlist against everything else it knows about for the next 72 hours. It reports each approach closer than 10 km with:
- the time of closest approach (TCA)
- the miss distance and relative speed
- how old each element set will be at that moment, since older data is less certain

The app does this in the background every 6 hours. `orbtrack conjunctions` lists the results.

## How it works

`src/orbtrack/engine/conjunctions.py`

1. **Altitude filter:** two objects can only meet if their altitude bands overlap. Each primary keeps only the objects whose perigee–apogee band comes within 40 km of its own. That leaves about 160 objects for a sun-synchronous satellite and about 10,500 for Hubble, which shares its altitude with a Starlink shell.
2. **Coarse pass:** everything is propagated together with vectorised SGP4 at a fixed step:
   - 60 s in low orbit, where relative speeds reach 15 km/s
   - 5 minutes higher up, where they are a few hundred m/s at most

   Each pair's distance curve is scanned for dips. A dip is kept if a close approach could hide between two samples, that is, if its sampled distance is under the threshold plus half a step of relative motion.
3. **Refinement:** each kept dip is refined with Newton steps on Δr · Δv = 0, using SGP4 itself. This pins the TCA to a few milliseconds.
4. **Skips:** docked vehicles and formation flyers, with relative speed under 10 m/s, are left out. Their "approach" is permanent, not an event.

## Checked against SOCRATES

CelesTrak's [SOCRATES Plus](https://celestrak.org/SOCRATES/) screens the whole catalog every day, and for each conjunction it publishes the exact element sets it used. `orbtrack conjunctions socrates` takes the closest SOCRATES conjunctions for each watchlist satellite, downloads those element sets, and recomputes them with OrbTrack's screening. Run of 24 Oct 2026, 4 per satellite:

```
  TCA (UTC)            range km  Δt s    Δrange m  Δspeed m/s  pair
  10-30 02:58:12.271    0.339  -0.000      -0.3       +0.1   HST × STARLINK-4395
  10-27 01:51:29.984    0.906  +0.000      +0.2       +0.2   CRYOSAT 2 × NOAA 16 DEB
  10-27 21:44:00.946    0.998  -0.000      -0.1       +0.3   SUOMI NPP × FENGYUN 1C DEB
  10-27 10:05:32.844    0.506  +0.000      -0.0       -0.1   LANDSAT 8 × METEOR 2-7 DEB
  10-29 21:19:56.860    1.383  +0.000      +0.3       -0.3   NOAA 20 (JPSS-1) × SL-8 DEB
  10-25 01:41:56.304    3.725  +0.000      -0.0       +0.3   CSS (TIANHE) × APEX ARIES 1
  10-28 21:06:01.172    0.577  +0.000      -0.2       -0.1   LANDSAT 9 × DAQI-2
  … (25 rows in all)
25 of 25 reproduced (TCA within 1 s and range within 50 m)
```

- **Phase 5a gate (known close approaches reproduced): met.**
- **Accuracy:** every TCA agrees to the millisecond. Every miss distance agrees to within half a metre, which is SOCRATES' own rounding.
- **No ISS or geostationary rows:** the ISS's only approaches under 5 km were its docked vehicles, which are skipped. The five geostationary satellites had none.

The offline tests check the same thing without a network: two orbits built to cross, compared against a brute-force search at 1-second steps. They cover several passes, chunk edges and docked pairs.

## Coverage: what gets screened against

CelesTrak publishes no full catalog. OrbTrack screens against every active satellite (about 16,800), plus the four big debris clouds CelesTrak does publish: Cosmos 1408, Fengyun 1C, Iridium 33 and Cosmos 2251. That's about 19,500 objects in total, against about 32,800 in SOCRATES.

How many of SOCRATES' 7-day conjunctions under 5 km involve an object OrbTrack screens against:

| Watchlist | Conjunctions | Covered |
| --- | --- | --- |
| Hubble | 187 | 187 (all Starlink) |
| ISS, Tiangong | 7 | 5 |
| NOAA 20, Suomi NPP, Landsats, CryoSat 2 | 87 | 35 |
| Geostationary weather satellites | 0 | — |
| **All** | **281** | **227 (81%)** |

- **Low Earth observation satellites are where the gap shows.** Most of their close calls are with old debris and spent stages (NOAA 16 debris, Meteor debris, SL-8 rocket bodies…) that CelesTrak doesn't publish.
- **Space-Track's full catalog would close that gap.** The client from Phase 4a is ready for it once a login is set.

## Limits

- **Not for real decisions.** TLEs are only good to a kilometre or so after a day, and the error grows with age. A 0.3 km miss predicted from data two days old could really be several km either way. That is why each approach carries the age of both element sets.
- **No collision probability.** Probability needs covariance (uncertainty) data, which public TLEs don't carry. SOCRATES' "maximum probability" assumes an uncertainty; OrbTrack doesn't pretend to have one.

## Commands

```bash
uv run orbtrack conjunctions                 # predicted approaches, nearest first (also: conjunctions ISS, --km 2)
uv run orbtrack conjunctions screen          # screen the watchlist now (about a minute)
uv run orbtrack conjunctions socrates        # recompute SOCRATES' closest ones and compare (--limit per satellite)
```
