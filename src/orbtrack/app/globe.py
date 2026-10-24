"""NiceGUI wrappers around globe.js and timebar.js."""

import time
from pathlib import Path

from nicegui import ui

STATIC_DIR = Path(__file__).parent / "static"


class Globe(ui.element, component="globe.js"):
    """The 3D globe. Python pushes data in; clicks and keys come back as events."""

    def init_catalog(self, payload: dict) -> None:
        self.run_method("initCatalog", payload)

    def push_frame(self, frame: dict) -> None:
        self.run_method("pushFrame", frame)

    def set_class_visible(self, index: int, visible: bool) -> None:
        self.run_method("setClassVisible", index, visible)

    def select(self, norad_id: int, trail: dict) -> None:
        self.run_method("select", norad_id, trail)

    def set_trail(self, trail: dict) -> None:
        self.run_method("setTrail", trail)

    def release(self) -> None:
        self.run_method("release")

    def enter_sky(self, observer_frame: dict) -> None:
        self.run_method("enterSky", observer_frame)

    def exit_sky(self) -> None:
        self.run_method("exitSky")

    def set_passes(self, passes: list[dict]) -> None:
        self.run_method("setPasses", passes)

    def look_at(self, azimuth_deg: float, elevation_deg: float) -> None:
        self.run_method("lookAt", azimuth_deg, elevation_deg)


class Timebar(ui.element, component="timebar.js"):
    """The time machine bar along the bottom."""

    def set_clock(self, state: dict) -> None:
        self.run_method("setClock", state, time.time() * 1000)

    def set_bands(self, bands: dict) -> None:
        self.run_method("setBands", bands)

    def set_passes(self, passes: list[dict]) -> None:
        self.run_method("setPasses", passes)
