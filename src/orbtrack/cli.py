"""Command line: `orbtrack app`, `where ISS`, `passes ISS`, `catalog`, `history sync | status | show ISS`,
`events [ISS] | detect | train | evaluate`."""

import argparse
import sys
from pathlib import Path
from collections import Counter

import numpy as np
from skyfield.api import load, wgs84

from orbtrack.config import Observer, home_observer, spacetrack_credentials
from orbtrack.engine import Catalog, next_passes, positions


def _pick(catalog: Catalog, query: str):
    hits = catalog.find(query)
    if not hits:
        sys.exit(f"No satellite matches {query!r}.")
    return hits[0]


def cmd_where(args, catalog: Catalog) -> None:
    sat = _pick(catalog, args.satellite)
    t = catalog.ts.now()
    geo = sat.sat.at(t)
    lat, lon = wgs84.latlon_of(geo)
    age_h = (t - sat.sat.epoch) * 24
    print(f"{sat.name} (NORAD {sat.norad_id}, {sat.orbit_class})")
    print(f"  time       {t.utc_strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"  latitude   {lat.degrees:8.3f}°")
    print(f"  longitude  {lon.degrees:8.3f}°")
    print(f"  altitude   {wgs84.height_of(geo).km:8.1f} km")
    print(f"  speed      {np.linalg.norm(geo.velocity.km_per_s):8.3f} km/s")
    print(f"  TLE age    {age_h:8.1f} h")


def cmd_passes(args, catalog: Catalog) -> None:
    sat = _pick(catalog, args.satellite)
    observer = home_observer()
    if args.lat is not None and args.lon is not None:
        observer = Observer("Custom", args.lat, args.lon, args.elevation)
    passes = next_passes(
        sat, observer, catalog.ts.now(), hours=args.hours, min_elevation_deg=args.min_elevation
    )
    print(f"{sat.name} over {observer.name} ({observer.latitude:.3f}, {observer.longitude:.3f}),"
          f" next {args.hours:g} h, above {args.min_elevation:g}°")
    if not passes:
        print("  no passes")
    for p in passes:
        mark = "visible" if p.visible else ""
        print(f"  {p.rise:%Y-%m-%d %H:%M:%S}  max {p.max_elevation_deg:5.1f}°"
              f"  {p.duration_s / 60:4.1f} min  az {p.rise_azimuth_deg:5.1f}°→{p.set_azimuth_deg:5.1f}°"
              f"  {mark}")
    print("  (times UTC)")


def cmd_catalog(args, catalog: Catalog) -> None:
    pos = positions(catalog, catalog.ts.now())
    ok = np.isfinite(pos.altitude_km[:, 0])
    print(f"{len(catalog)} satellites in '{args.group}', {ok.sum()} propagated cleanly")
    for cls, n in Counter(s.orbit_class for s in catalog).most_common():
        print(f"  {cls:9s} {n}")


def cmd_app(args) -> None:
    from orbtrack.app.main import serve

    serve(group=args.group, port=args.port, show=not args.no_browser)


def cmd_history(args) -> None:
    from datetime import UTC, datetime, timedelta

    from orbtrack.data.celestrak import fetch_group
    from orbtrack.data.history import EARTH_RADIUS_KM, HistoryStore
    from orbtrack.data.sync import snapshot, sync_spacetrack

    store = HistoryStore()
    if args.action == "status":
        st = store.status()
        print(f"{st['path']}\n  {st['rows']:,} element sets for {st['satellites']:,} satellites,"
              f" {st['watchlist']} on the Space-Track watchlist")
        for row in st["by_source"]:
            print(f"  {row['source']:10s} {row['rows']:>10,} rows  {row['satellites']:>6,} sats"
                  f"  {row['first']:%Y-%m-%d} → {row['last']:%Y-%m-%d}")
        for source, at in st["last_sync"].items():
            print(f"  last {source} sync {at:%Y-%m-%d %H:%M} UTC")
        return
    if args.action == "export":
        print(f"Parquet written to {store.export_parquet(Path(args.target or 'history-parquet'))}")
        return
    if args.action == "sync":
        print(snapshot(store, fetch_group(args.group)))
        credentials = spacetrack_credentials()
        if credentials is None:
            print("spacetrack: skipped (add SPACETRACK_IDENTITY and SPACETRACK_PASSWORD to .env)")
        else:
            print(sync_spacetrack(store, credentials, days=args.days or 365))
        return

    catalog = Catalog.from_celestrak(args.group, load.timescale())
    if args.action in ("watch", "unwatch"):
        if not args.target:
            print("\n".join(f"  {n:>6}  {s.name if (s := catalog.get(n)) else '?'}" for n in store.watchlist()))
            return
        sat = _pick(catalog, args.target)
        (store.watch if args.action == "watch" else store.unwatch)([sat.norad_id])
        print(f"{'Watching' if args.action == 'watch' else 'Stopped watching'} {sat.name} (NORAD {sat.norad_id})")
        return

    # show
    sat = _pick(catalog, args.target or "ISS")
    args.days = args.days or 30
    series = store.series(sat.norad_id, since=datetime.now(UTC) - timedelta(days=args.days))
    if not len(series["epoch"]):
        sys.exit(f"No history for {sat.name} yet: run `orbtrack history sync`.")
    mean_alt = series["semimajor_km"] - EARTH_RADIUS_KM
    print(f"{sat.name} (NORAD {sat.norad_id}), last {args.days} days: {len(mean_alt)} element sets")
    print("  epoch (UTC)          mean alt   perigee   apogee    change  source")
    for i, epoch in enumerate(series["epoch"]):
        change = mean_alt[i] - mean_alt[i - 1] if i else 0.0
        mark = "  ▲ raised" if change > 0.5 else "  ▼ lowered" if change < -2 else ""
        print(f"  {str(epoch)[:16].replace('T', ' ')}  {mean_alt[i]:8.2f}  {series['perigee_km'][i]:8.2f}"
              f"  {series['apogee_km'][i]:7.2f}  {change:+7.2f}  {series['source'][i]}{mark}")


def cmd_events(args) -> None:
    from datetime import UTC, datetime, timedelta

    from orbtrack.data.history import HistoryStore
    from orbtrack.detect import model

    if args.action == "train":
        path = model.default_model_path()
        path.unlink(missing_ok=True)
        detector = model.load_or_train(path)
        print(f"Trained on {detector.trained_on}, alert threshold {detector.threshold:.3f}, saved to {path}")
        print(model.evaluate(detector).text())
        return
    if args.action == "evaluate":
        print(model.evaluate(model.load_or_train()).text())
        return

    store = HistoryStore()
    if args.action == "detect":
        events = model.detect_watchlist(store, model.load_or_train())
        print(f"{len(events)} events in the history of {len(store.watchlist())} watched satellites")
        return

    # list
    norad_id = None
    if args.target:
        norad_id = _pick(Catalog.from_celestrak(args.group, load.timescale()), args.target).norad_id
    days = args.days or 30
    events = store.events(since=datetime.now(UTC) - timedelta(days=days), norad_id=norad_id)
    print(f"{len(events)} events in the last {days} days"
          + ("" if events else " (history builds up while the app runs; try `orbtrack events detect`)"))
    for e in events:
        print(f"  {e['epoch']:%Y-%m-%d %H:%M}  {(e['name'] or str(e['norad_id']))[:22]:22s}  {e['kind']:12s}"
              f"  {e['confidence']:4.0%}  {e['reason']}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="orbtrack", description=__doc__)
    parser.add_argument("--group", default="active", help="CelesTrak group (default: active)")
    sub = parser.add_subparsers(dest="command", required=True)

    where = sub.add_parser("where", help="where a satellite is right now")
    where.add_argument("satellite", help="name fragment or NORAD id")
    where.set_defaults(func=cmd_where)

    passes = sub.add_parser("passes", help="upcoming passes over you")
    passes.add_argument("satellite")
    passes.add_argument("--hours", type=float, default=48)
    passes.add_argument("--min-elevation", type=float, default=10)
    passes.add_argument("--lat", type=float)
    passes.add_argument("--lon", type=float)
    passes.add_argument("--elevation", type=float, default=0, help="metres")
    passes.set_defaults(func=cmd_passes)

    catalog = sub.add_parser("catalog", help="summary of the catalog")
    catalog.set_defaults(func=cmd_catalog)

    app = sub.add_parser("app", help="open the live 3D globe in your browser")
    app.add_argument("--port", type=int, default=8080)
    app.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    app.set_defaults(func=cmd_app)

    history = sub.add_parser("history", help="local orbit history (Space-Track + CelesTrak snapshots)")
    history.add_argument("action", choices=["sync", "status", "show", "watch", "unwatch", "export"])
    history.add_argument("target", nargs="?", help="satellite for show/watch/unwatch, folder for export")
    history.add_argument("--days", type=int, help="show: how far back (30); sync: Space-Track backfill (365)")
    history.set_defaults(func=cmd_history)

    events = sub.add_parser("events", help="maneuvers and decays found in the orbit history")
    events.add_argument("action", nargs="?", default="list", choices=["list", "detect", "train", "evaluate"])
    events.add_argument("target", nargs="?", help="list: only this satellite")
    events.add_argument("--days", type=int, help="list: how far back (30)")
    events.set_defaults(func=cmd_events)

    args = parser.parse_args(argv)
    if args.func in (cmd_app, cmd_history, cmd_events):
        args.func(args)
    else:
        args.func(args, Catalog.from_celestrak(args.group, load.timescale()))


if __name__ == "__main__":
    main()
