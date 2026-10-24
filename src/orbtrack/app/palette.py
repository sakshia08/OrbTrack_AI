"""Ctrl+K command palette: turns a few typed words into an action, with no language model.

``suggest`` ranks fixed commands (views, time controls, orbit filters) by keyword, adds
time jumps like "+2h" and speeds like "60x", and then satellites by name or NORAD id.
"""

import re
from dataclasses import dataclass

from orbtrack.engine import Catalog


@dataclass(frozen=True)
class Command:
    label: str
    hint: str
    action: str  # follow, view, solo, show_all, rate, toggle, live, shift, release, surprise, soon
    arg: object = None
    icon: str = "bolt"


# (keywords, command). Keywords are matched as prefixes of what is typed and vice versa.
FIXED: list[tuple[tuple[str, ...], Command]] = [
    (("sky", "your sky", "passes", "passes tonight", "tonight", "overhead", "above me", "look up"),
     Command("Your sky", "look up from your city · tonight's passes", "view", "sky", "visibility")),
    (("globe", "earth", "space", "orbit view"),
     Command("Globe", "back to the whole Earth", "view", "globe", "public")),
    (("starlink",), Command("Starlink only", "solo one orbit class", "solo", "STARLINK", "grain")),
    (("leo", "low earth"), Command("Low Earth orbit only", "solo one orbit class", "solo", "LEO", "blur_circular")),
    (("meo", "medium earth", "gps", "navigation", "galileo", "glonass"),
     Command("Medium Earth orbit only", "GPS, Galileo, GLONASS, BeiDou", "solo", "MEO", "satellite_alt")),
    (("geo", "geostationary"), Command("Geostationary only", "solo one orbit class", "solo", "GEO", "radio_button_checked")),
    (("heo", "molniya", "elliptical", "highly elliptical"),
     Command("Highly elliptical only", "solo one orbit class", "solo", "HEO", "all_inclusive")),
    (("all", "show all", "everything", "reset filters"),
     Command("Show every orbit", "turn all classes back on", "show_all", None, "select_all")),
    (("pause", "stop", "freeze", "play", "resume"), Command("Pause / play", "space", "toggle", None, "play_pause")),
    (("live", "now", "real time", "reset time"), Command("Back to live", "L", "live", None, "sensors")),
    (("fast forward", "faster", "timelapse", "1000x"), Command("Fast forward 1000×", "→ steps up", "rate", 1000, "fast_forward")),
    (("rewind", "reverse", "backwards"), Command("Rewind 300×", "← steps down", "rate", -300, "fast_rewind")),
    (("tomorrow",), Command("Jump to tomorrow", "+24 h", "shift", 86400, "update")),
    (("yesterday",), Command("Jump to yesterday", "−24 h", "shift", -86400, "history")),
    (("release", "unfollow", "stop following", "let go"), Command("Let go", "Esc", "release", None, "open_with")),
    (("surprise", "random", "surprise me"), Command("Surprise me", "follow a random satellite", "surprise", None, "casino")),
    (("maneuvers", "manoeuvres", "anomalies", "events", "decay"),
     Command("Maneuvers this week", "arrives in phase 4", "soon", None, "query_stats")),
    (("conjunctions", "close approaches", "collisions"),
     Command("Close approaches", "arrives in phase 5", "soon", None, "crisis_alert")),
]

DEFAULTS = ["Your sky", "Fast forward 1000×", "Starlink only", "Surprise me", "Back to live"]
FEATURED = [25544, 48274, 20580]  # ISS, Tiangong, Hubble
# Catalog names are terse ("HST", "CSS (TIANHE)"), so a few everyday names point at the right one.
ALIASES = {
    "hubble": 20580,
    "tiangong": 48274,
    "chinese space station": 48274,
    "space station": 25544,
    "international space station": 25544,
}

SHIFT = re.compile(r"^([+-])\s*(\d+(?:\.\d+)?)\s*(m|min|mins|minutes?|h|hr|hrs|hours?|d|days?)$")
RATE = re.compile(r"^(-?\d+)\s*[x×]$|^[x×]\s*(-?\d+)$")
UNIT_S = {"m": 60, "h": 3600, "d": 86400}


def _keyword_score(query: str, keywords: tuple[str, ...]) -> int | None:
    best = None
    for kw in keywords:
        if kw == query:
            score = 0
        elif len(query) >= 2 and kw.startswith(query):
            score = 1
        elif len(kw) >= 3 and kw in query:
            score = 2
        else:
            continue
        best = score if best is None else min(best, score)
    return best


def _satellite_command(sat) -> Command:
    return Command(sat.name, f"NORAD {sat.norad_id} · {sat.orbit_class}", "follow", sat.norad_id, "satellite_alt")


def _duration_text(seconds: float) -> str:
    sign = "+" if seconds >= 0 else "−"
    seconds = abs(seconds)
    if seconds >= 86400:
        return f"{sign}{seconds / 86400:g} d"
    if seconds >= 3600:
        return f"{sign}{seconds / 3600:g} h"
    return f"{sign}{seconds / 60:g} min"


def suggest(query: str, catalog: Catalog, limit: int = 8) -> list[Command]:
    q = " ".join(query.lower().split())
    if not q:
        by_label = {cmd.label: cmd for _, cmd in FIXED}
        featured = [_satellite_command(s) for n in FEATURED if (s := catalog.get(n))]
        return (featured + [by_label[label] for label in DEFAULTS])[:limit]

    out: list[Command] = []
    if m := SHIFT.match(q):
        seconds = float(m.group(2)) * UNIT_S[m.group(3)[0]] * (1 if m.group(1) == "+" else -1)
        out.append(Command(f"Jump {_duration_text(seconds)}", "time machine", "shift", seconds, "update"))
    if m := RATE.match(q):
        rate = int(m.group(1) or m.group(2))
        if rate != 0 and abs(rate) <= 1000:
            out.append(Command(f"Run at {rate}×", "time machine", "rate", rate, "speed"))

    scored = sorted(
        (score, i, cmd) for i, (kws, cmd) in enumerate(FIXED) if (score := _keyword_score(q, kws)) is not None
    )
    close = [cmd for score, _, cmd in scored if score <= 1]
    loose = [cmd for score, _, cmd in scored if score > 1]  # a keyword merely inside the query
    aliased, found = [], []
    if len(q) >= 2 or q.isdigit():
        hits = (catalog.get(n) for alias, n in ALIASES.items() if len(q) >= 3 and alias.startswith(q))
        aliased = list(dict.fromkeys(s for s in hits if s))
        found = [s for s in catalog.find(q)[:limit] if s not in aliased]
    out += close + [_satellite_command(s) for s in aliased] + loose + [_satellite_command(s) for s in found]
    return out[:limit]
