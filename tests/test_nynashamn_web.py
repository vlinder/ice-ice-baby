from datetime import date

import pytest

from adapters import nynashamn_web as a
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 2)
H = "nynashamn-hll-arena"


def _raw():
    return {"page": (FIXTURES / "nynashamn_web.html").read_text(encoding="utf-8")}


@pytest.fixture(scope="module")
def result():
    return a.parse(_raw(), TODAY)


def _day(r, d):
    return [(s.start, s.end, s.stick) for s in r.halls[H].sessions if s.date == d]


def test_hall_and_covers(result):
    assert list(result.halls) == [H]
    assert (result.covers_from, result.covers_to) == ("2026-11-02", "2026-11-29")
    assert result.source_updated == "2026-10-30T16:00"
    assert result.warnings == []
    assert result.empty_reason is None
    assert len(result.halls[H].sessions) == 24


def test_split_sub_sessions(result):
    assert _day(result, "2026-11-02") == []
    assert _day(result, "2026-11-06") == [("15:00", "16:00", "no_stick"), ("16:00", "16:45", "with_stick")]
    assert _day(result, "2026-11-07") == [("18:00", "18:45", "no_stick"), ("18:45", "19:30", "with_stick")]
    assert _day(result, "2026-11-08") == [("10:00", "10:45", "no_stick"), ("10:45", "11:45", "with_stick")]


def test_cancelled_and_inconsistent_change_in_december():
    r = a.parse(_raw(), date(2026, 12, 1))
    assert (r.covers_from, r.covers_to) == ("2026-12-01", "2026-12-28")
    assert _day(r, "2026-12-11") == [("15:00", "16:00", "no_stick"), ("16:00", "16:45", "with_stick")]
    assert _day(r, "2026-12-12") == []  # inställd (hockeycup)
    # "Ny tid 8.30-12.00 (8.30-10.30 ..., 10.30-12.30 ...)" does not add up: warn, emit nothing
    assert _day(r, "2026-12-13") == []
    assert len(r.warnings) == 1 and "13 december" in r.warnings[0]
    assert _day(r, "2026-12-20") == [("10:00", "10:45", "no_stick"), ("10:45", "11:45", "with_stick")]


def test_cancellations_in_new_year():
    r = a.parse(_raw(), date(2027, 1, 15))
    assert _day(r, "2027-01-17") == []
    assert _day(r, "2027-02-05")
    assert _day(r, "2027-02-06") == [] and _day(r, "2027-02-07") == []
    assert r.warnings == []


def test_consistent_change_replaces_template():
    raw = _raw()
    raw["page"] = raw["page"].replace("10.30-12.30 – med", "10.30-12.00 – med")
    r = a.parse(raw, date(2026, 12, 1))
    s = [x for x in r.halls[H].sessions if x.date == "2026-12-13"]
    assert [(x.start, x.end, x.stick, x.changed) for x in s] == [
        ("08:30", "10:30", "no_stick", True), ("10:30", "12:00", "with_stick", True)]
    assert r.warnings == []


def test_season_start_and_end():
    r = a.parse(_raw(), date(2026, 8, 25))
    assert r.covers_from == "2026-09-11"
    assert min(s.date for s in r.halls[H].sessions) == "2026-09-11"
    early = a.parse(_raw(), date(2026, 8, 1))
    assert early.halls[H].sessions == [] and early.empty_reason
    late = a.parse(_raw(), date(2027, 4, 20))
    assert late.covers_to == "2027-04-30"
    over = a.parse(_raw(), date(2027, 5, 10))
    assert over.halls[H].sessions == [] and over.empty_reason


@pytest.mark.parametrize("text,expected", [
    ("15.00-16.45 (15.00-16.00 utan puck och klubba, 16.00 – 16.45 med puck och klubba).",
     [("15:00", "16:00", "no_stick", None), ("16:00", "16:45", "with_stick", None)]),
    ("10.00-11.45 (10.00 – 10.45 utan puck och klubba. 10.45 – 11.45 med puck och klubba)",
     [("10:00", "10:45", "no_stick", None), ("10:45", "11:45", "with_stick", None)]),
    ("12.00-13.00 med puck och klubba", [("12:00", "13:00", "with_stick", None)]),
    ("12.00-13.00 familjeåkning", [("12:00", "13:00", "unknown", "familjeåkning")]),
])
def test_split_sessions(text, expected):
    assert a.split_sessions(text) == expected


def test_typo_in_weekly_line_becomes_warning():
    raw = _raw()
    raw["page"] = raw["page"].replace("18.00-19.30 (18.00-18.45", "18.00-19.30 (18.00-18-45")
    r = a.parse(raw, TODAY)
    assert len(r.warnings) == 1 and "Lördagar" in r.warnings[0]
    assert _day(r, "2026-11-07") == []
    assert _day(r, "2026-11-08")


def test_layout_change_fails_loudly():
    raw = _raw()
    raw["page"] = raw["page"].replace("Tider för allmänhetens åkning säsongen", "Allmänhetens åkning")
    with pytest.raises(ParseError):
        a.parse(raw, TODAY)
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><p>Ny design!</p></body></html>"}, TODAY)
