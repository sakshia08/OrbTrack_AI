"""A catalog of satellites built from OMM records (CelesTrak or Space-Track)."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import cached_property

from sgp4.api import SatrecArray
from skyfield.api import EarthSatellite, Timescale, load

MU_KM3_S2 = 398600.4418
EARTH_RADIUS_KM = 6378.137


def orbit_class(name: str, mean_motion: float, eccentricity: float) -> str:
    """Bucket an orbit as STARLINK, LEO, MEO, GEO or HEO (used for colouring)."""
    if "STARLINK" in name.upper():
        return "STARLINK"
    if eccentricity > 0.25:
        return "HEO"
    n_rad_s = mean_motion * 2 * 3.141592653589793 / 86400
    altitude_km = (MU_KM3_S2 / n_rad_s**2) ** (1 / 3) - EARTH_RADIUS_KM
    if altitude_km < 2000:
        return "LEO"
    if 0.98 < mean_motion < 1.02:
        return "GEO"
    return "MEO"


@dataclass(frozen=True)
class Satellite:
    norad_id: int
    name: str
    orbit_class: str
    sat: EarthSatellite


class Catalog:
    """Satellites keyed by NORAD id, with a vectorised SGP4 array for bulk propagation."""

    def __init__(self, records: Iterable[dict], ts: Timescale | None = None):
        self.ts = ts or load.timescale()
        self._by_id: dict[int, Satellite] = {}
        for record in records:
            try:
                sat = EarthSatellite.from_omm(self.ts, record)
            except (KeyError, ValueError):
                continue
            norad_id = int(record["NORAD_CAT_ID"])
            self._by_id[norad_id] = Satellite(
                norad_id=norad_id,
                name=record["OBJECT_NAME"],
                orbit_class=orbit_class(
                    record["OBJECT_NAME"],
                    float(record["MEAN_MOTION"]),
                    float(record["ECCENTRICITY"]),
                ),
                sat=sat,
            )

    @classmethod
    def from_celestrak(cls, group: str = "active", ts: Timescale | None = None) -> "Catalog":
        from orbtrack.data.celestrak import fetch_group

        return cls(fetch_group(group), ts)

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[Satellite]:
        return iter(self._by_id.values())

    def get(self, norad_id: int) -> Satellite | None:
        return self._by_id.get(norad_id)

    def find(self, query: str) -> list[Satellite]:
        """Satellites matching a NORAD id or a case-insensitive name fragment.

        An exact name match comes first, so "ISS" finds the ISS before its modules.
        """
        query = query.strip()
        if query.isdigit():
            hit = self.get(int(query))
            return [hit] if hit else []
        needle = query.upper()
        hits = [s for s in self if needle in s.name.upper()]
        return sorted(hits, key=lambda s: (not s.name.upper().startswith(needle), len(s.name)))

    @cached_property
    def satrec_array(self) -> SatrecArray:
        return SatrecArray([s.sat.model for s in self])
