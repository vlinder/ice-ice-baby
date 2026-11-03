from datetime import date

import pytest

from adapters import lidingo_web as a
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 2)
ARENA, VALLEN, ANNEX = "lidingo-exakt-arena", "lidingo-lidingovallens-rink", "lidingo-exakt-annex"


def _raw():
    return {"page": (FIXTURES / "lidingo_web.html").read_text(encoding="utf-8")}


@pytest.fixture(scope="module")
def result():
    return a.parse(_raw(), TODAY)


def _day(r, hid, d):
    return [(s.start, s.end, s.stick) for s in r.halls[hid].sessions if s.date == d]


def test_halls_and_covers(result):
    assert set(result.halls) == {ARENA, VALLEN, ANNEX}
    assert (result.covers_from, result.covers_to) == ("2026-11-02", "2026-11-29")
    assert result.warnings == []
    assert result.empty_reason is None
    assert result.source_updated is None
    assert {k: len(v.sessions) for k, v in result.halls.items()} == {ARENA: 19, VALLEN: 40, ANNEX: 20}


def test_exakt_arena_sessions(result):
    assert _day(result, ARENA, "2026-11-02") == []  # no Monday times
    assert _day(result, ARENA, "2026-11-04") == [("08:00", "14:45", "no_stick")]
    assert _day(result, ARENA, "2026-11-08") == [("10:00", "11:15", "with_stick")]


def test_cancelled_date_skipped(result):
    # "Allmänhetens åkning utgår: 22 november" (a Sunday)
    assert _day(result, ARENA, "2026-11-22") == []
    assert _day(result, ARENA, "2026-11-29") == [("10:00", "11:15", "with_stick")]
    # the 22 November exception belongs to Exakt arena only
    assert _day(result, VALLEN, "2026-11-22") == [("12:00", "18:00", "no_stick")]


def test_continuation_rows_and_mixed_wording(result):
    s = [x for x in result.halls[VALLEN].sessions if x.date == "2026-11-03"]
    assert [(x.start, x.end, x.stick, x.note) for x in s] == [
        ("09:00", "15:00", "with_stick", "Med/utan klubba och puck"),
        ("19:00", "20:45", "no_stick", None),
    ]
    assert _day(result, ANNEX, "2026-11-07") == []  # weekdays only


def test_red_days_closed():
    # window 21 Dec - 17 Jan contains Christmas, New Year and Epiphany
    r = a.parse(_raw(), date(2026, 12, 21))
    days = {s.date for s in r.halls[ANNEX].sessions}
    for d in ("2026-12-25", "2027-01-01", "2027-01-06"):
        assert d not in days
    assert "2026-12-28" in days
    # Lidingövallen also lists 5 January as cancelled
    assert not _day(r, VALLEN, "2027-01-05")
    assert _day(r, VALLEN, "2027-01-04")


def test_seasons_cut_window():
    # Exakt annex closes after week 13 2027 (Sun 4 April); Lidingövallen after week 9
    r = a.parse(_raw(), date(2027, 3, 29))
    assert max(s.date for s in r.halls[ANNEX].sessions) == "2027-04-02"
    assert r.halls[VALLEN].sessions == []
    assert r.halls[ARENA].sessions
    assert r.empty_reason is None


@pytest.mark.parametrize("year,expected", [
    (2026, {date(2026, 4, 3), date(2026, 4, 5), date(2026, 4, 6), date(2026, 5, 14),
            date(2026, 5, 24), date(2026, 6, 20), date(2026, 10, 31)}),
    (2027, {date(2027, 3, 26), date(2027, 3, 28), date(2027, 3, 29), date(2027, 5, 6),
            date(2027, 5, 16), date(2027, 6, 26), date(2027, 11, 6)}),
])
def test_moving_holidays(year, expected):
    assert expected <= a.swedish_public_holidays(year)


def test_season_text_variants():
    assert a._season("öppen från höstlovet till och med vecka 9", TODAY) == (date(2026, 10, 26), date(2027, 3, 7))
    assert a._season("från vecka 40 2026 till och med vecka 13 2027", TODAY) == (date(2026, 9, 28), date(2027, 4, 4))
    assert a._season("öppen från vecka 32 2026 och erbjuder allmänhetens åkning från vecka 34 fram till "
                     "och med vecka 21 2027", TODAY) == (date(2026, 8, 17), date(2027, 5, 30))


def test_bad_time_and_unknown_deviation_become_warnings():
    raw = _raw()
    raw["page"] = raw["page"].replace("08.00–14.45", "08.00–14-45", 1)
    raw["page"] = raw["page"].replace("* 22 november,", "* 22 nov-ish,", 1)
    r = a.parse(raw, TODAY)
    assert len(r.warnings) == 3  # bad time, fullscreen copy differs, unreadable deviation
    assert any("14-45" in w for w in r.warnings)
    assert any("22 nov-ish" in w for w in r.warnings)
    assert _day(r, ARENA, "2026-11-04") == []


def test_missing_hall_section_fails_loudly():
    raw = _raw()
    raw["page"] = raw["page"].replace("Exakt annex – allmänhetens åkning", "Exakt annex")
    with pytest.raises(ParseError):
        a.parse(raw, TODAY)


def test_layout_change_fails_loudly():
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><main><h2>Ny design</h2></main></body></html>"}, TODAY)


# The page on 2026-11-03: dated rows ("3 november | Stängt") in Exakt arena's weekly table.
def _dated():
    return a.parse({"page": (FIXTURES / "lidingo_web_dated.html").read_text(encoding="utf-8")},
                             date(2026, 11, 3))


def test_dated_closed_rows_remove_the_weekly_times():
    r = _dated()
    assert r.warnings == []
    arena = {s.date for s in r.halls["lidingo-exakt-arena"].sessions}
    assert "2026-11-03" not in arena and "2026-11-14" not in arena
    assert "2026-11-04" in arena and "2026-11-10" in arena  # other Tuesdays/Wednesdays unchanged


def test_unreadable_dated_row_shows_nothing_that_day():
    page = (FIXTURES / "lidingo_web_dated.html").read_text(encoding="utf-8")
    assert page.count("Stängt") >= 2  # 3 and 14 November in the table (and its fullscreen copy)
    r = a.parse({"page": page.replace("Stängt", "Öppet hus?")}, date(2026, 11, 3))
    assert r.warnings and all(w.startswith("lidingo-exakt-arena:") for w in r.warnings)
    assert "2026-11-03" not in {s.date for s in r.halls["lidingo-exakt-arena"].sessions}
