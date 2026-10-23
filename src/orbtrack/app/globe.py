"""NiceGUI wrapper around globe.js."""

from pathlib import Path

from nicegui import ui

STATIC_DIR = Path(__file__).parent / "static"


class Globe(ui.element, component="globe.js"):
    """The 3D globe. Python pushes data in; clicks and keys come back as events."""

    def init_catalog(self, payload: dict) -> None:
        self.run_method("initCatalog", payload)

    def push_frame(self, frame: dict) -> None:
        self.run_method("pushFrame", frame)

    def set_sun(self, lat: float, lng: float) -> None:
        self.run_method("setSun", lat, lng)

    def set_class_visible(self, index: int, visible: bool) -> None:
        self.run_method("setClassVisible", index, visible)

    def select(self, norad_id: int, trail: dict, follow: bool = True) -> None:
        self.run_method("select", norad_id, trail, follow)

    def set_trail(self, trail: dict) -> None:
        self.run_method("setTrail", trail)

    def release(self) -> None:
        self.run_method("release")
