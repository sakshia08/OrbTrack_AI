"""The OrbTrack page: a full-screen live globe with a glass HUD on top."""

import logging
import random
import time
from datetime import UTC, datetime, timedelta

from nicegui import app, run, ui

from orbtrack.app import frames, jobs, palette
from orbtrack.app.clock import WINDOW_S, SimClock
from orbtrack.app.globe import STATIC_DIR, Globe, Timebar
from orbtrack.app.jobs import state
from orbtrack.app.sparkline import altitude_sparkline, sparkline_caption
from orbtrack.config import home_observer
from orbtrack.engine import Catalog

log = logging.getLogger("orbtrack")

HEARTBEAT_S = 0.2  # how often the page checks whether it owes the browser a new frame
SKY_PERIOD_S = 2.0
BANDS_PERIOD_S = 1800.0
HISTORY_DAYS = 90  # how far back the altitude sparkline on the card looks

CLASS_COLORS = ["#38e1ff", "#9b8cff", "#ffc24b", "#ff5fa2", "#9dff6a"]  # matches globe.js
CLASS_LABELS = {"LEO": "Low Earth", "STARLINK": "Starlink", "MEO": "Medium Earth", "GEO": "Geostationary", "HEO": "Highly elliptical"}
TARGETS = [("ISS", 25544), ("TIANGONG", 48274), ("HUBBLE", 20580)]
NICKNAMES = {norad_id: label.title() if label != "ISS" else label for label, norad_id in TARGETS}

FONTS = (
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700'
    '&family=JetBrains+Mono:wght@300;400;500;600;700&display=swap" rel="stylesheet">'
)

def div(classes: str = "", text: str | None = None) -> ui.element:
    if text is not None:
        return ui.label(text).classes(classes)
    return ui.element("div").classes(classes)


def short_name(norad_id: int, name: str) -> str:
    """"Hubble" rather than "HST", "Tiangong" rather than "CSS (TIANHE)"."""
    return NICKNAMES.get(norad_id) or name.split(" (")[0]


def local_hm(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000).astimezone().strftime("%H:%M")


@ui.page("/")
def index() -> None:
    catalog: Catalog = state["catalog"]
    loaded = {"version": state["version"]}  # which catalog refresh this page is showing
    ts = catalog.ts
    observer = home_observer()
    observer_frame = frames.observer_frame(observer)
    ui.add_head_html(FONTS)
    ui.add_css(STATIC_DIR.parent / "style.css")

    clock = SimClock()
    globe = Globe()
    # pass_from: simulated time the next pass was computed at (None until it has been)
    selected: dict = {"sat": None, "pass": None, "pass_from": None, "pass_busy": False, "trail_t0": None}
    view = {"mode": "globe"}
    push = {"ready": False, "dirty": True, "busy": False, "last": 0.0}
    sky = {"passes": [], "from": None, "ids": (), "busy": False}
    visible = [True] * len(frames.ORBIT_CLASSES)

    def sim_time():
        return ts.from_datetime(datetime.fromtimestamp(clock.now(), tz=UTC))

    # Loading veil, lifted when the first frame with satellites is on screen
    with div("loading") as loading:
        ui.html('<div class="word">ORB<span>TRACK</span></div><div class="msg">Acquiring orbits</div>', sanitize=False)

    # Brand + live status
    with div("glass brand"):
        ui.html('<div class="word">ORB<span>TRACK</span></div>', sanitize=False)
        status = ui.html("", sanitize=False).classes("sub")

    # Search and view switch, top centre
    with div("topbar"):
        with div("glass pill search").on("click", lambda: open_palette()):
            ui.icon("search")
            ui.label("Search").classes("search-text")
            div("kbd", "Ctrl K")
        with div("glass pill views"):
            view_buttons = {
                "globe": div("view", "GLOBE").on("click", lambda: set_view("globe")),
                "sky": div("view", "YOUR SKY").on("click", lambda: set_view("sky")),
            }

    # Legend with per-class toggles, and quick targets
    legend_rows, count_labels = [], {}
    with div("glass legend") as legend:
        div("title", "ORBITS")
        for index, name in enumerate(frames.ORBIT_CLASSES):
            with div("legend-row") as row:
                ui.element("div").classes("dot").style(
                    f"background:{CLASS_COLORS[index]};box-shadow:0 0 10px {CLASS_COLORS[index]}"
                )
                ui.label(CLASS_LABELS[name])
                count_labels[name] = div("count", "")
            row.on("click", lambda _, i=index: set_visible(i, not visible[i]))
            legend_rows.append(row)
        div("title quick-title", "QUICK TARGETS")
        target_buttons = {}
        with div("targets"):
            for label, norad_id in TARGETS:
                if catalog.get(norad_id):
                    target_buttons[norad_id] = div("target", label).on("click", lambda _, n=norad_id: select(n))
            div("target", "SURPRISE ME").on("click", lambda: surprise())

    # Your sky panel, in place of the legend while looking up
    with div("glass skypanel gone") as skypanel:
        div("title", "YOUR SKY")
        div("place", f"{observer.name} · {abs(observer.latitude):.2f}°{'N' if observer.latitude >= 0 else 'S'} "
                     f"{abs(observer.longitude):.2f}°{'E' if observer.longitude >= 0 else 'W'}")
        with div("above"):
            above_count = div("big", "—")
            div("cap", "satellites above your horizon")
        sun_line = div("sun", "")
        div("title passes-title", "PASSES · NEXT 24 H")
        pass_list = div("passes")
        div("hint", "CLICK A PASS TO WATCH IT · DRAG TO LOOK AROUND")

    # Followed-satellite card
    with div("glass card hidden") as card:
        kicker = div("kicker", "FOLLOWING")
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
        with div("history gone") as history:
            div("k", f"ALTITUDE · LAST {HISTORY_DAYS} DAYS")
            spark = ui.html("", sanitize=False).classes("spark")
            spark_caption = div("caption", "")
        div("hint", "SCROLL TO ZOOM · ESC TO RELEASE")

    timebar = Timebar()

    # Command palette
    pal = {"items": [], "index": 0, "open": False}
    with div("palette-veil") as veil:
        with div("glass palette"):
            with div("palette-input"):
                ui.icon("search").classes("palette-glyph")
                pinput = ui.input(placeholder="Satellites, views, time… try “+6h”, “60x”, “starlink”").props(
                    "borderless dense dark"
                ).classes("palette-field")
                div("kbd", "ESC")
            results = div("palette-results")
            div("palette-foot", "↑ ↓ choose · ENTER run · works offline, no AI needed")
    veil.on("click.self", lambda: close_palette())

    # --- Behaviour ------------------------------------------------------------

    def update_status() -> None:
        if clock.is_live:
            status.content = f'<span class="live-dot"></span>Live · {len(catalog):,} objects'
        else:
            rate = clock.effective_rate
            label = "paused" if rate == 0 else f"{rate:g}×"
            status.content = f'<span class="live-dot amber"></span>Time machine · {label}'

    def changed() -> None:
        """The clock moved by hand: resend the frame, the trail and the clock right away."""
        push["dirty"] = True
        selected["trail_t0"] = None
        timebar.set_clock(clock.state())
        update_status()
        update_card()

    def set_visible(index: int, on: bool) -> None:
        visible[index] = on
        legend_rows[index].classes(remove="off") if on else legend_rows[index].classes(add="off")
        globe.set_class_visible(index, on)

    def update_card() -> None:
        sat = selected["sat"]
        if sat is None:
            return
        t = sim_time()
        tm = frames.telemetry(sat, t)
        cells["alt"].content = f"{tm['alt_km']:,.0f}<small>km</small>"
        cells["speed"].content = f"{tm['speed_km_s']:.2f}<small>km/s</small>"
        cells["lat"].content = f"{abs(tm['lat']):.2f}°<small>{'N' if tm['lat'] >= 0 else 'S'}</small>"
        cells["lon"].content = f"{abs(tm['lon']):.2f}°<small>{'E' if tm['lon'] >= 0 else 'W'}</small>"
        cells["period"].content = f"{tm['period_min']:.0f}<small>min</small>"
        cells["age"].content = f"{tm['tle_age_h']:.0f}<small>h</small>"
        if selected["pass_from"] is not None:
            next_pass.text = frames.pass_text(selected["pass"], t.utc_datetime())

    async def refresh_next_pass() -> None:
        """Find the selected satellite's next pass, again whenever the clock leaves it behind."""
        sat = selected["sat"]
        if sat is None or selected["pass_busy"]:
            return
        selected["pass_busy"] = True
        t = sim_time()
        try:
            p = await run.io_bound(frames.next_pass, sat, observer, t)
        finally:
            selected["pass_busy"] = False
        if selected["sat"] is sat:
            selected.update({"pass": p, "pass_from": t.utc_datetime().timestamp()})
            update_card()

    async def select(norad_id: int) -> None:
        sat = catalog.get(int(norad_id))
        if sat is None:
            return
        selected.update({"sat": sat, "pass": None, "pass_from": None})
        t = sim_time()
        globe.select(sat.norad_id, frames.trail(sat, t))
        selected["trail_t0"] = clock.now()
        name.text = sat.name
        meta.text = f"NORAD {sat.norad_id} · {CLASS_LABELS[sat.orbit_class]}"
        kicker.text = "TRACKING IN YOUR SKY" if view["mode"] == "sky" else "FOLLOWING"
        for nid, button in target_buttons.items():
            button.classes(add="active") if nid == sat.norad_id else button.classes(remove="active")
        update_card()
        card.classes(remove="hidden")
        next_pass.text = "computing…"
        await refresh_next_pass()
        await show_history(sat)
        if view["mode"] == "sky":
            await refresh_sky(force=True)

    async def show_history(sat) -> None:
        """The altitude sparkline, from whatever history the local store holds for this satellite."""
        since = datetime.now(UTC) - timedelta(days=HISTORY_DAYS)
        series = await run.io_bound(jobs.store().series, sat.norad_id, since)
        if selected["sat"] is not sat or series is None:
            return
        altitude = series["semimajor_km"] - frames.EARTH_RADIUS_KM
        svg = altitude_sparkline(series["epoch"], altitude)
        if svg is None:
            history.classes(add="gone")
            return
        spark.content = svg
        spark_caption.text = sparkline_caption(series["epoch"], altitude)
        history.classes(remove="gone")

    def update_counts() -> None:
        counts = {name: 0 for name in frames.ORBIT_CLASSES}
        for sat in catalog:
            counts[sat.orbit_class] += 1
        for name, label in count_labels.items():
            label.text = f"{counts[name]:,}"

    async def swap_catalog() -> None:
        """A fresh catalog arrived (every 2 h): hand it to the browser without a reload."""
        nonlocal catalog
        catalog = state["catalog"]
        loaded["version"] = state["version"]
        globe.init_catalog(frames.catalog_payload(catalog))
        update_counts()
        update_status()
        push["dirty"] = True
        if selected["sat"] is not None:
            await select(selected["sat"].norad_id)

    async def surprise() -> None:
        await select(random.choice(list(catalog)).norad_id)

    def release() -> None:
        selected.update({"sat": None, "pass": None, "pass_from": None})
        globe.release()
        card.classes(add="hidden")
        for button in target_buttons.values():
            button.classes(remove="active")

    async def set_view(mode: str) -> None:
        if mode == view["mode"]:
            return
        view["mode"] = mode
        for key, button in view_buttons.items():
            button.classes(add="active") if key == mode else button.classes(remove="active")
        if mode == "sky":
            globe.enter_sky(observer_frame)
            legend.classes(add="gone")
            skypanel.classes(remove="gone")
            kicker.text = "TRACKING IN YOUR SKY"
            await refresh_sky(force=True)
        else:
            globe.exit_sky()
            skypanel.classes(add="gone")
            legend.classes(remove="gone")
            kicker.text = "FOLLOWING"

    def watched_ids() -> tuple[int, ...]:
        ids = [n for _, n in TARGETS]
        if selected["sat"] is not None:
            ids.insert(0, selected["sat"].norad_id)
        return tuple(dict.fromkeys(ids))

    async def refresh_sky(force: bool = False) -> None:
        """Recompute passes when the clock leaves the 24 h they cover, or the watched set changes."""
        now = clock.now()
        stale = sky["from"] is None or not (sky["from"] - 60 <= now <= sky["from"] + 20 * 3600)
        if sky["busy"] or not (force or stale or sky["ids"] != watched_ids()):
            return
        sky["busy"] = True
        try:
            ids = watched_ids()
            passes = await run.io_bound(frames.sky_passes, catalog, list(ids), observer, sim_time())
            if passes is None:  # the page went away mid-call
                return
            for p in passes:
                p["label"] = short_name(p["norad_id"], p["name"])
            sky.update(passes=passes, ids=ids)
            sky["from"] = now
        finally:
            sky["busy"] = False
        public = [{k: v for k, v in p.items() if k != "pass"} for p in passes]
        globe.set_passes(public)
        timebar.set_passes([{k: p[k] for k in ("label", "rise_ms", "set_ms", "visible")} for p in passes])
        render_passes()

    def render_passes() -> None:
        pass_list.clear()
        now_ms = clock.now() * 1000
        with pass_list:
            upcoming = [p for p in sky["passes"] if p["set_ms"] > now_ms][:8]
            if not upcoming:
                div("empty", "No passes above 10° in the next 24 h")
            for p in upcoming:
                live = p["rise_ms"] <= now_ms
                with div("pass-row" + (" now" if live else "")).on("click", lambda _, p=p: watch(p)):
                    div("pname", p["label"])
                    div("ptime", "NOW" if live else local_hm(p["rise_ms"]))
                    div("pel", f"{p['max_el']:.0f}°")
                    div("pvis" + (" yes" if p["visible"] else ""), "VISIBLE" if p["visible"] else "—")

    async def watch(p: dict) -> None:
        """Jump to just before a pass, run at 10× and track the satellite across the sky."""
        clock.jump(p["rise_ms"] / 1000 - 45)
        clock.set_rate(10)
        changed()
        if view["mode"] != "sky":
            await set_view("sky")
        globe.look_at(p["rise_az"], 8)
        await select(p["norad_id"])

    async def update_sky_panel() -> None:
        if view["mode"] != "sky":
            return
        t = sim_time()
        count = await run.io_bound(frames.above_horizon, catalog, observer, t)
        if count is None:
            return
        above_count.text = f"{count:,}"
        sun_alt = frames.sun_altitudes(observer, t, 0)["alt"][0]
        sky_word = "daylight" if sun_alt > 0 else "twilight" if sun_alt > -12 else "dark sky"
        sun_line.text = f"SUN {sun_alt:+.0f}° · {sky_word}"
        render_passes()

    async def push_frame() -> None:
        t, clock_state, span = sim_time(), clock.state(), clock.frame_span()
        frame = await run.io_bound(frames.keyframes, catalog, t, span)
        if frame is None:  # io_bound gives None when the page went away mid-call
            return
        frame["clock"], frame["sent_ms"] = clock_state, time.time() * 1000
        globe.push_frame(frame)
        sat = selected["sat"]
        if sat is not None:
            period_s = frames.period_minutes(sat) * 60
            if selected["trail_t0"] is None or abs(clock.now() - selected["trail_t0"]) > period_s / 3:
                globe.set_trail(frames.trail(sat, t))
                selected["trail_t0"] = clock.now()

    async def heartbeat() -> None:
        if clock.clamp():
            changed()
            ui.notify("Reached the edge of the ±48 h window: orbit data gets less accurate beyond it.", type="warning")
        if not push["ready"] or push["busy"] or not (push["dirty"] or time.time() - push["last"] >= clock.frame_period()):
            return
        push.update(busy=True, dirty=False, last=time.time())
        try:
            if loaded["version"] != state["version"]:
                await swap_catalog()
            await push_frame()
        finally:
            push["busy"] = False

    async def tick() -> None:
        update_card()
        p, since, now = selected["pass"], selected["pass_from"], clock.now()
        if selected["sat"] is None or since is None:
            return
        passed = p.set.timestamp() if p is not None else since + 47 * 3600
        if now > passed or now < since - 60:  # the clock left the pass behind, or rewound past it
            await refresh_next_pass()

    async def sky_timer() -> None:
        if sky["from"] is None:
            return  # not before first_frame has computed them once
        await refresh_sky()
        await update_sky_panel()

    def push_bands() -> None:
        start = ts.from_datetime(datetime.fromtimestamp(time.time() - WINDOW_S - 3600, tz=UTC))
        timebar.set_bands(frames.sun_altitudes(observer, start, 2 * WINDOW_S / 3600 + 2))

    async def first_frame(e) -> None:
        loading.classes(add="done")
        log.warning("OrbTrack: first frame with satellites at %s ms after page load", e.args["ms"])
        # The slower extras wait until the globe is up, so they never hold up the first frame.
        push_bands()
        await refresh_sky(force=True)

    # Time machine controls
    def step(e) -> None:
        clock.step(e.args["dir"])
        changed()

    def toggle() -> None:
        clock.toggle()
        changed()

    def go_live() -> None:
        clock.live()
        changed()

    def scrub(e) -> None:
        clock.jump(e.args["sim_ms"] / 1000)
        changed()

    # Command palette
    def render_results() -> None:
        results.clear()
        with results:
            for i, cmd in enumerate(pal["items"]):
                row = div("palette-row" + (" active" if i == pal["index"] else "") + (" soon" if cmd.action == "soon" else ""))
                with row:
                    ui.icon(cmd.icon).classes("palette-icon")
                    div("palette-label", cmd.label)
                    div("palette-hint", cmd.hint)
                row.on("click", lambda _, c=cmd: run_command(c))

    def refresh_palette(query: str) -> None:
        pal["items"] = palette.suggest(query or "", catalog)
        pal["index"] = 0
        render_results()

    def open_palette() -> None:
        pal["open"] = True
        veil.classes(add="open")
        pinput.value = ""
        refresh_palette("")
        pinput.run_method("focus")

    def close_palette() -> None:
        pal["open"] = False
        veil.classes(remove="open")
        pinput.run_method("blur")

    def move(delta: int) -> None:
        if pal["items"]:
            pal["index"] = (pal["index"] + delta) % len(pal["items"])
            render_results()

    async def run_selected() -> None:
        if pal["items"]:
            await run_command(pal["items"][pal["index"]])

    async def run_command(cmd: palette.Command) -> None:
        close_palette()
        match cmd.action:
            case "follow":
                await select(cmd.arg)
            case "view":
                await set_view(cmd.arg)
            case "solo":
                for i, cls in enumerate(frames.ORBIT_CLASSES):
                    set_visible(i, cls == cmd.arg)
            case "show_all":
                for i in range(len(visible)):
                    set_visible(i, True)
            case "rate":
                clock.set_rate(cmd.arg)
                changed()
            case "toggle":
                toggle()
            case "live":
                go_live()
            case "shift":
                clock.shift(cmd.arg)
                changed()
            case "release":
                release()
            case "surprise":
                await surprise()
            case "soon":
                ui.notify(f"{cmd.label}: {cmd.hint}.", type="info")

    pinput.on_value_change(lambda e: refresh_palette(e.value))
    pinput.on("keydown.down", lambda: move(1))
    pinput.on("keydown.up", lambda: move(-1))
    pinput.on("keydown.enter", run_selected)
    pinput.on("keydown.esc", close_palette)

    async def hotkey(e) -> None:
        key = e.args["key"]
        if key == "palette":
            open_palette()
        elif key == "toggle":
            toggle()
        elif key in ("faster", "slower"):
            clock.step(1 if key == "faster" else -1)
            changed()
        elif key == "live":
            go_live()
        elif key == "view":
            await set_view("globe" if view["mode"] == "sky" else "sky")
        elif key == "escape":
            if pal["open"]:
                close_palette()
            elif selected["sat"] is not None:
                release()
            elif view["mode"] == "sky":
                await set_view("globe")

    globe.on("select", lambda e: select(e.args["norad_id"]))
    globe.on("hotkey", hotkey)
    globe.on("first_frame", first_frame)
    timebar.on("step", step)
    timebar.on("toggle", toggle)
    timebar.on("live", go_live)
    timebar.on("scrub", scrub)

    async def globe_ready() -> None:
        # Sent once three.js and globe.gl are loaded, so the big catalog never competes with them.
        if push["ready"]:
            return
        globe.init_catalog(frames.catalog_payload(catalog))
        push["ready"] = True
        await heartbeat()

    def hello() -> None:
        # A call that lands before Vue has mounted the globe is dropped, so ask until it answers.
        if not push["ready"]:
            globe.run_method("hello")

    def start() -> None:
        hello()
        update_counts()
        view_buttons["globe"].classes(add="active")
        timebar.set_clock(clock.state())
        update_status()

    globe.on("ready", globe_ready)
    # Timers only start once the browser is connected, so the first one-shot can send data.
    ui.timer(0, start, once=True)
    ui.timer(1.0, hello)
    ui.timer(HEARTBEAT_S, heartbeat)
    ui.timer(0.5, tick)
    ui.timer(SKY_PERIOD_S, sky_timer)
    ui.timer(BANDS_PERIOD_S, push_bands)


def serve(group: str = "active", port: int = 8080, show: bool = True) -> None:
    state["group"] = group
    app.add_static_files("/orbtrack-static", STATIC_DIR)
    app.on_startup(jobs.load_catalog)
    app.on_startup(jobs.start)
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
