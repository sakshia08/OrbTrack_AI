import pytest

from orbtrack.app.palette import suggest


def first(query, catalog):
    return suggest(query, catalog)[0]


def test_empty_query_offers_featured_satellites_and_views(catalog):
    items = suggest("", catalog)
    assert items[0].action == "follow" and items[0].arg == 25544
    assert any(c.label == "Your sky" for c in items)


@pytest.mark.parametrize(
    ("query", "action", "arg"),
    [
        ("iss", "follow", 25544),
        ("ISS", "follow", 25544),
        ("25544", "follow", 25544),
        ("passes tonight", "view", "sky"),
        ("your sky", "view", "sky"),
        ("globe", "view", "globe"),
        ("starlink", "solo", "STARLINK"),
        ("gps", "solo", "MEO"),
        ("show all", "show_all", None),
        ("pause", "toggle", None),
        ("live", "live", None),
        ("60x", "rate", 60),
        ("x1000", "rate", 1000),
        ("-300x", "rate", -300),
        ("+6h", "shift", 6 * 3600),
        ("-30 min", "shift", -1800),
        ("+2d", "shift", 2 * 86400),
        ("tomorrow", "shift", 86400),
        ("stop following", "release", None),
        ("maneuvers this week", "soon", None),
        ("surprise me", "surprise", None),
    ],
)
def test_queries_map_to_actions(catalog, query, action, arg):
    cmd = first(query, catalog)
    assert (cmd.action, cmd.arg) == (action, arg)


@pytest.mark.parametrize(("query", "norad_id"), [("tiang", 48274), ("tianhe", 48274), ("chinese", 48274), ("space station", 25544)])
def test_everyday_names_and_fragments_find_satellites(catalog, query, norad_id):
    assert first(query, catalog).arg == norad_id


def test_nonsense_finds_nothing_and_does_not_crash(catalog):
    assert suggest("zzzqqq", catalog) == []
    assert suggest("0x", catalog) == []
