"""Command line: `orbtrack app`, `orbtrack where ISS`, `orbtrack passes ISS`, `orbtrack catalog`."""

import argparse
import sys
from collections import Counter

import numpy as np
from skyfield.api import load, wgs84

from orbtrack.config import Observer, home_observer
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

    args = parser.parse_args(argv)
    if args.func is cmd_app:
        cmd_app(args)
    else:
        args.func(args, Catalog.from_celestrak(args.group, load.timescale()))


if __name__ == "__main__":
    main()
