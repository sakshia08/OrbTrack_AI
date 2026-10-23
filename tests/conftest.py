import json
from pathlib import Path

import pytest
from skyfield.api import load

from orbtrack.config import Observer
from orbtrack.engine import Catalog

FIXTURES = Path(__file__).parent / "fixtures"

# Frozen CelesTrak "stations" group (ISS epoch 2026-10-22 23:36 UTC), so tests run offline
# and give the same answers forever.
BANGALORE = Observer("Bangalore", 12.9716, 77.5946, 920.0)


@pytest.fixture(scope="session")
def ts():
    return load.timescale()


@pytest.fixture(scope="session")
def stations_records():
    return json.loads((FIXTURES / "stations_omm.json").read_text())


@pytest.fixture(scope="session")
def catalog(stations_records, ts):
    return Catalog(stations_records, ts)


@pytest.fixture(scope="session")
def iss(catalog):
    return catalog.get(25544)


@pytest.fixture(scope="session")
def start(ts):
    return ts.utc(2026, 10, 23)
