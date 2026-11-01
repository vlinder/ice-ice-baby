from datetime import date

import pytest

from adapters import tyreso_rbok as a
from adapters.common import ParseError
from conftest import FIXTURES


def raw():
    item = (FIXTURES / "tyreso_rbok_item.html").read_text(encoding="utf-8")
    return {"resurser": (FIXTURES / "tyreso_rbok_resurser.json").read_text(), "item0": item, "item1": item}


def test_parses_both_halls_and_dedupes_repeated_pages():
    r = a.parse(raw(), date(2026, 11, 1))
    assert set(r.halls) == {"tyreso-tyreso-ishall", "tyreso-fontanahallen"}
    assert len(r.halls["tyreso-tyreso-ishall"].sessions) == 6
    assert len(r.halls["tyreso-fontanahallen"].sessions) == 6
    assert (r.covers_from, r.covers_to) == ("2026-11-01", "2026-11-14")


def test_stick_rules():
    r = a.parse(raw(), date(2026, 11, 1))
    first = r.halls["tyreso-tyreso-ishall"].sessions[0]
    assert (first.date, first.start, first.end, first.stick) == ("2026-11-04", "16:00", "16:45", "with_stick")
    sat = [s for s in r.halls["tyreso-tyreso-ishall"].sessions if s.date == "2026-11-07"][0]
    assert sat.stick == "no_stick"


def test_year_rollover():
    html = """<table><thead><tr><td>Datum</td><td>Tid</td><td>Resurs</td><td>Information</td></tr></thead>
    <tbody><tr><td>Lör 02 Jan</td><td>12:00 - 13:45</td><td>Tyresövallen / Tyresö Ishall / Ishall</td><td>Utan klubba</td></tr></tbody></table>"""
    r = a.parse({"item0": html}, date(2026, 12, 28))
    assert r.halls["tyreso-tyreso-ishall"].sessions[0].date == "2027-01-02"


def test_missing_table_fails_loudly():
    with pytest.raises(ParseError):
        a.parse({"item0": "<div>Underhåll pågår</div>"}, date(2026, 11, 1))
