"""The OrbTrack page: a full-screen live globe with a glass HUD on top."""

import logging
import random

from nicegui import app, run, ui

from orbtrack.app import frames
from orbtrack.app.globe import STATIC_DIR, Globe
from orbtrack.config import home_observer
from orbtrack.engine import Catalog

log = logging.getLogger("orbtrack")

FRAME_PERIOD_S = 2.0  # how often fresh keyframes are pushed
FRAME_AHEAD_S = 4.0  # keyframe B is this far ahead of A, so the browser never runs out
TRAIL_PERIOD_S = 2.0
SUN_PERIOD_S = 60.0

CLASS_COLORS = ["#38e1ff", "#9b8cff", "#ffc24b", "#ff5fa2", "#9dff6a"]  # matches globe.js
CLASS_LABELS = {"LEO": "Low Earth", "STARLINK": "Starlink", "MEO": "Medium Earth", "GEO": "Geostationary", "HEO": "Highly elliptical"}
TARGETS = [("ISS", 25544), ("TIANGONG", 48274), ("HUBBLE", 20580)]

FONTS = (
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700'
    '&family=JetBrains+Mono:wght@300;400;500;600;700&display=swap" rel="stylesheet">'
)

state: dict = {"catalog": None, "group": "active"}


def load_catalog() -> None:
    state["catalog"] = Catalog.from_celestrak(state["group"])
    log.warning("OrbTrack: %d satellites loaded from CelesTrak '%s'", len(state["catalog"]), state["group"])


def div(classes: str = "", text: str | None = None) -> ui.element:
    if text is not None:
        return ui.label(text).classes(classes)
    return ui.element("div").classes(classes)


@ui.page("/")
def index() -> None:
    catalog: Catalog = state["catalog"]
    ts = catalog.ts
    observer = home_observer()
    ui.add_head_html(FONTS)
    ui.add_css(STATIC_DIR.parent / "style.css")

    globe = Globe()
    selected: dict = {"sat": None}

    # Loading veil, lifted when the first frame with satellites is on screen
    with div("loading") as loading:
        ui.html('<div class="word">ORB<span>TRACK</span></div><div class="msg">Acquiring orbits</div>', sanitize=False)

    # Brand + clock
    with div("glass brand"):
        ui.html('<div class="word">ORB<span>TRACK</span></div>', sanitize=False)
        clock = div("clock", "--:--:--")
        ui.html(f'<div class="sub"><span class="live-dot"></span>Live · {len(catalog):,} objects · UTC</div>', sanitize=False)

    # Legend with per-class toggles
    counts = {name: 0 for name in frames.ORBIT_CLASSES}
    for sat in catalog:
        counts[sat.orbit_class] += 1
    with div("glass legend"):
        div("title", "ORBITS")
        for index, name in enumerate(frames.ORBIT_CLASSES):
            with div("legend-row") as row:
                ui.element("div").classes("dot").style(
                    f"background:{CLASS_COLORS[index]};box-shadow:0 0 10px {CLASS_COLORS[index]}"
                )
                ui.label(CLASS_LABELS[name])
                div("count", f"{counts[name]:,}")

            def toggle(_, row=row, index=index) -> None:
                row.classes(toggle="off")
                globe.set_class_visible(index, "off" not in row.classes)

            row.on("click", toggle)

    # Followed-satellite card
    with div("glass card hidden") as card:
        div("kicker", "FOLLOWING")
        name = div("name", "")
        meta = div("meta", "")
        with div("grid"):
            cells = {}
            for key, label in [("alt", "ALTITUDE"), ("speed", "VELOCITY"), ("lat", "LATITUDE"), ("lon", "LONGITUDE"), ("period", "PERIOD"), ("age", "TLE AGE")]:
                with div():
                    div("k", label)
                    cells[key] = ui.html("", sanitize=False).classes("v")
        with div("pass"):
            div("k", f"NEXT PASS OVER {observer.name.upper()}")
            next_pass = div("", "")
        div("hint", "SCROLL TO ZOOM · ESC TO RELEASE")

    # Quick targets
    target_buttons = {}
    with div("glass targets"):
        for label, norad_id in TARGETS:
            if catalog.get(norad_id):
                target_buttons[norad_id] = div("target", label).on("click", lambda _, n=norad_id: select(n))
        div("target", "SURPRISE ME").on("click", lambda: select(random.choice(list(catalog)).norad_id))

    def update_card() -> None:
        sat = selected["sat"]
        if sat is None:
            return
        tm = frames.telemetry(sat, ts.now())
        cells["alt"].content = f"{tm['alt_km']:,.0f}<small>km</small>"
        cells["speed"].content = f"{tm['speed_km_s']:.2f}<small>km/s</small>"
        cells["lat"].content = f"{abs(tm['lat']):.2f}°<small>{'N' if tm['lat'] >= 0 else 'S'}</small>"
        cells["lon"].content = f"{abs(tm['lon']):.2f}°<small>{'E' if tm['lon'] >= 0 else 'W'}</small>"
        cells["period"].content = f"{tm['period_min']:.0f}<small>min</small>"
        cells["age"].content = f"{tm['tle_age_h']:.0f}<small>h</small>"

    async def select(norad_id: int) -> None:
        sat = catalog.get(int(norad_id))
        if sat is None:
            return
        selected["sat"] = sat
        globe.select(sat.norad_id, frames.trail(sat, ts.now()), follow=True)
        name.text = sat.name
        meta.text = f"NORAD {sat.norad_id} · {CLASS_LABELS[sat.orbit_class]}"
        for nid, button in target_buttons.items():
            button.classes(add="active") if nid == sat.norad_id else button.classes(remove="active")
        update_card()
        card.classes(remove="hidden")
        next_pass.text = "computing…"
        text = await run.io_bound(frames.next_pass_text, sat, observer, ts.now())
        if selected["sat"] is sat:
            next_pass.text = text

    def release() -> None:
        selected["sat"] = None
        globe.release()
        card.classes(add="hidden")
        for button in target_buttons.values():
            button.classes(remove="active")

    async def push_frame() -> None:
        frame = await run.io_bound(frames.keyframes, catalog, ts.now(), FRAME_AHEAD_S)
        globe.push_frame(frame)

    def push_sun() -> None:
        globe.set_sun(*frames.subsolar_point(ts.now()))

    def push_trail() -> None:
        if selected["sat"] is not None:
            globe.set_trail(frames.trail(selected["sat"], ts.now()))

    def tick() -> None:
        clock.text = ts.now().utc_strftime("%H:%M:%S")
        update_card()

    def first_frame(e) -> None:
        loading.classes(add="done")
        log.warning("OrbTrack: first frame with satellites at %s ms after page load", e.args["ms"])

    globe.on("select", lambda e: select(e.args["norad_id"]))
    globe.on("release", lambda _: release())
    globe.on("first_frame", first_frame)

    async def start() -> None:
        globe.init_catalog(frames.catalog_payload(catalog))
        push_sun()
        await push_frame()
        tick()

    # Timers only start once the browser is connected, so the first one-shot can send data.
    ui.timer(0, start, once=True)
    ui.timer(FRAME_PERIOD_S, push_frame)
    ui.timer(1.0, tick)
    ui.timer(TRAIL_PERIOD_S, push_trail)
    ui.timer(SUN_PERIOD_S, push_sun)

def serve(group: str = "active", port: int = 8080, show: bool = True) -> None:
    state["group"] = group
    app.add_static_files("/orbtrack-static", STATIC_DIR)
    app.on_startup(load_catalog)
    ui.run(
        title="OrbTrack",
        port=port,
        dark=True,
        reload=False,
        show=show,
        favicon="🛰️",
        show_welcome_message=True,
        reconnect_timeout=10.0,  # loading WebGL can stall slow machines for a few seconds
    )
