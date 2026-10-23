import pytest

from orbtrack.engine import orbit_class


def test_loads_every_record(catalog, stations_records):
    assert len(catalog) == len(stations_records)


def test_find_prefers_exact_name(catalog):
    assert catalog.find("iss")[0].norad_id == 25544


def test_find_by_norad_id(catalog):
    assert [s.name for s in catalog.find("25544")] == ["ISS (ZARYA)"]


def test_find_nothing(catalog):
    assert catalog.find("no such satellite") == []


@pytest.mark.parametrize(
    ("name", "mean_motion", "ecc", "expected"),
    [
        ("ISS (ZARYA)", 15.49, 0.0007, "LEO"),
        ("STARLINK-1234", 15.06, 0.0001, "STARLINK"),
        ("GPS BIIF-1", 2.005, 0.01, "MEO"),
        ("INTELSAT 901", 1.0027, 0.0002, "GEO"),
        ("MOLNIYA 1-91", 2.006, 0.70, "HEO"),
    ],
)
def test_orbit_class(name, mean_motion, ecc, expected):
    assert orbit_class(name, mean_motion, ecc) == expected
