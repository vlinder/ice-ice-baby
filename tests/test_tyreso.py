from datetime import date

import pytest

from adapters import tyreso_rbok as a
from adapters.common import ParseError
from conftest import FIXTURES

BOARD = a.BOARDS[0]


def raw():
    return {BOARD: (FIXTURES / "tyreso_rbok_item.html").read_text(encoding="utf-8")}


def test_parses_both_halls():
    r = a.parse(raw(), date(2026, 11, 1))
    assert set(r.halls) == {"tyreso-tyreso-ishall", "tyreso-fontanahallen"}
    assert len(r.halls["tyreso-tyreso-ishall"].sessions) == 6
    assert len(r.halls["tyreso-fontanahallen"].sessions) == 6
    assert (r.covers_from, r.covers_to) == ("2026-11-01", "2026-11-14")
    assert r.halls["tyreso-tyreso-ishall"].sessions[0].source_url == a.SOURCE_URL


def test_stick_rules():
    r = a.parse(raw(), date(2026, 11, 1))
    first = r.halls["tyreso-tyreso-ishall"].sessions[0]
    assert (first.date, first.start, first.end, first.stick) == ("2026-11-04", "16:00", "16:45", "with_stick")
    sat = [s for s in r.halls["tyreso-tyreso-ishall"].sessions if s.date == "2026-11-07"][0]
    assert sat.stick == "no_stick"


def test_year_rollover():
    html = """<table><thead><tr><td>Datum</td><td>Tid</td><td>Resurs</td><td>Information</td></tr></thead>
    <tbody><tr><td>Lör 02 Jan</td><td>12:00 - 13:45</td><td>Tyresövallen / Tyresö Ishall / Ishall</td><td>Utan klubba</td></tr></tbody></table>"""
    r = a.parse({BOARD: html}, date(2026, 12, 28))
    assert r.halls["tyreso-tyreso-ishall"].sessions[0].date == "2027-01-02"


def test_missing_table_fails_loudly():
    with pytest.raises(ParseError):
        a.parse({BOARD: "<div>Underhåll pågår</div>"}, date(2026, 11, 1))
