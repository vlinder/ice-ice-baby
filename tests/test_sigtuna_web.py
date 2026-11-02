from datetime import date

import pytest

from adapters import sigtuna_web as a
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 2)
A, B = "sigtuna-pinbackshallen-a-hallen", "sigtuna-pinbackshallen-b-hallen"


@pytest.fixture(scope="module")
def raw():
    return {"page": (FIXTURES / "sigtuna_web.html").read_text(encoding="utf-8")}


@pytest.fixture(scope="module")
def result(raw):
    return a.parse(raw, TODAY)


def _day(r, hid, d):
    return [(s.start, s.end, s.stick) for s in r.halls[hid].sessions if s.date == d]


def test_halls_counts_covers(result):
    assert {k: len(v.sessions) for k, v in result.halls.items()} == {A: 16, B: 27}
    assert (result.covers_from, result.covers_to) == ("2026-11-02", "2026-11-29")
    assert result.warnings == [] and result.source_updated is None and result.empty_reason is None
    assert (result.halls[A].name, result.halls[A].rink) == ("Pinbackshallen", "A-hallen")


def test_default_stick_per_hall_and_override(result):
    assert _day(result, A, "2026-11-02") == [("13:15", "15:15", "no_stick")]
    assert _day(result, B, "2026-11-08") == [("08:00", "09:45", "with_stick"), ("14:15", "18:45", "no_stick")]


def test_ej_dates_skip_one_week_only(result):
    for d in ("2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"):
        assert _day(result, B, d) == []
    assert _day(result, B, "2026-11-09") == [("13:15", "15:15", "with_stick")]
    assert _day(result, B, "2026-11-11") == [("08:00", "12:00", "with_stick")]


def test_saturday_slot_only_from_week_50(raw, result):
    assert _day(result, A, "2026-11-07") == []
    r = a.parse(raw, date(2026, 12, 1))
    assert _day(r, A, "2026-12-05") == []
    assert _day(r, A, "2026-12-12") == [("08:00", "09:00", "no_stick")]


def test_season_end(raw):
    r = a.parse(raw, date(2027, 2, 22))
    assert (r.covers_from, r.covers_to) == ("2027-02-22", "2027-03-07")  # Sunday of week 9
    r = a.parse(raw, date(2027, 3, 20))
    assert r.empty_reason and not r.halls[A].sessions


def test_current_holiday_block_replaces_weekly_times(raw):
    # On 28 October the höstlov block (26/10-1/11) is current and wins over the weekly table.
    r = a.parse(raw, date(2026, 10, 28))
    assert _day(r, A, "2026-10-28") == [("13:00", "16:00", "no_stick")]
    assert _day(r, A, "2026-10-31") == []          # Stängt för allmänheten
    assert _day(r, A, "2026-11-01") == [("08:00", "09:00", "no_stick")]
    assert _day(r, B, "2026-10-30") == [("09:00", "16:00", "with_stick"), ("20:00", "22:00", "no_stick")]
    assert _day(r, A, "2026-11-02") == [("13:15", "15:15", "no_stick")]
    assert any("09.00-14-00" in w for w in r.warnings)   # typo on 29 October
    assert _day(r, A, "2026-10-29") == []


def _page(ordinary_b_cell: str) -> dict:
    def table(cell):
        return f"<table><tbody><tr><td><p>Måndag</p></td><td>{cell}</td></tr></tbody></table>"
    return {"page": f"""<html><body><h2>Allmänhetens åkning</h2>
      <h3>Ordinarie öppettider för allmänhetens åkning</h3>
      <p>Tiderna gäller från och med vecka 42 år 2026 till vecka 9 år 2027.</p>
      <h4>A-hallen</h4><p>I A-hallen är det klubb-, puck- och bollförbud om ingenting annat anges.</p>
      {table('<p>13.15–15.15</p>')}
      <h4>B-hallen</h4><p>I B-hallen är det tillåtet med klubba, puck och bollar om ingenting annat anges.</p>
      {table(ordinary_b_cell)}
      <h2>Låna utrustning</h2></body></html>"""}


def test_unknown_qualifier_is_warned_and_skipped():
    r = a.parse(_page("<p>13.15–15.15 endast udda veckor</p><p>16.00-17.00</p>"), TODAY)
    assert [s.start for s in r.halls[B].sessions if s.date == "2026-11-02"] == ["16:00"]
    assert len(r.warnings) == 1 and "udda" in r.warnings[0]


def test_typo_is_warned():
    r = a.parse(_page("<p>13.300–15.15</p>"), TODAY)
    assert not r.halls[B].sessions and len(r.warnings) == 1


def test_missing_rink_is_layout_change():
    raw = _page("<p>13.15–15.15</p>")
    raw["page"] = raw["page"].replace("<h4>B-hallen</h4>", "<h4>C-hallen</h4>")
    with pytest.raises(ParseError):
        a.parse(raw, TODAY)


def test_unknown_default_rule_is_layout_change():
    raw = _page("<p>13.15–15.15</p>")
    raw["page"] = raw["page"].replace("tillåtet med klubba, puck och bollar", "som vanligt")
    with pytest.raises(ParseError):
        a.parse(raw, TODAY)


def test_garbage_page():
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><p>Ny design!</p></body></html>"}, TODAY)
