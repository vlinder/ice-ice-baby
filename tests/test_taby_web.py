from datetime import date

import pytest

from adapters import taby_web as a
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 2)
HAG = "taby-hagernas-ishall"
TIB = "taby-tibble-ishall"


def _raw():
    return {"page": (FIXTURES / "taby_web.html").read_text(encoding="utf-8")}


@pytest.fixture(scope="module")
def result():
    return a.parse(_raw(), TODAY)


def _s(result, hid, day):
    return [(x.start, x.end, x.stick) for x in result.halls[hid].sessions if x.date == day]


def test_halls_and_meta(result):
    assert set(result.halls) == {HAG, TIB}
    assert result.halls[HAG].name == "Hägernäs ishall"
    assert result.source_updated == "2026-10-31T00:00"


def test_typo_warns_and_is_skipped(result):
    assert result.warnings == ["taby-hagernas-ishall 2026-11-07: could not read 'Lördag 13.300–15.15 utan klubba'"]
    assert _s(result, HAG, "2026-11-07") == []


def test_covers_per_hall(result):
    assert (result.halls[HAG].covers_from, result.halls[HAG].covers_to) == ("2026-11-02", "2026-11-08")
    assert (result.halls[TIB].covers_from, result.halls[TIB].covers_to) == ("2026-11-02", "2026-11-29")
    assert (result.covers_from, result.covers_to) == ("2026-11-02", "2026-11-29")


def test_sessions(result):
    assert _s(result, HAG, "2026-11-02") == [("12:30", "14:15", "with_stick")]
    assert _s(result, HAG, "2026-11-08") == [("13:30", "15:15", "with_stick")]
    assert len(result.halls[HAG].sessions) == 6
    assert _s(result, TIB, "2026-11-02") == [("15:00", "15:45", "no_stick")]
    assert _s(result, TIB, "2026-11-07") == [("12:00", "13:45", "with_stick")]
    assert _s(result, TIB, "2026-11-29") == [("12:00", "13:45", "no_stick")]
    assert len(result.halls[TIB].sessions) == 28


def test_holiday_week_overrides_template_and_closed_day():
    r = a.parse(_raw(), date(2026, 10, 27))
    assert _s(r, TIB, "2026-10-27") == [("12:00", "13:45", "with_stick")]
    assert _s(r, TIB, "2026-10-31") == []  # "Lördag Stängd"
    assert (r.halls[TIB].covers_from, r.halls[TIB].covers_to) == ("2026-10-26", "2026-11-23")
    assert (r.halls[HAG].covers_from, r.halls[HAG].covers_to) == ("2026-10-26", "2026-11-08")


def test_hall_with_only_past_weeks_has_past_covers():
    r = a.parse(_raw(), date(2026, 11, 10))
    assert r.halls[HAG].sessions == []
    assert (r.halls[HAG].covers_from, r.halls[HAG].covers_to) == ("2026-11-02", "2026-11-08")
    assert r.warnings == []  # the typo is in a past week now


def _page(hag: str, tib: str = "<p><strong>Ordinarie tider, vecka 45–vecka 10</strong></p><p>Måndagar 15.00–15.45 utan klubba</p>") -> dict:
    def coll(name, i, body):
        return f"""<div><h3><a aria-controls="c{i}"><span>{name}</span></a></h3><div id="c{i}">{body}</div></div>"""
    return {"page": f"<html><body><main>{coll('Hägernäs ishall', 1, hag)}{coll('Tibble ishall', 2, tib)}</main></body></html>"}


def test_unknown_wording_and_dedupe():
    r = a.parse(_page("<p>Vecka 45</p><p>Måndag 12.30–14.15 familjeåkning</p><p>Måndag 12.30–14.15 familjeåkning</p>"), TODAY)
    assert [(x.date, x.stick, x.note) for x in r.halls[HAG].sessions] == [("2026-11-02", "unknown", "familjeåkning")]
    assert (r.halls[HAG].covers_from, r.halls[HAG].covers_to) == ("2026-11-02", "2026-11-02")


def test_unrecognised_block_heading_warns():
    r = a.parse(_page("<p>Vecka 45</p><p>Måndag 12.30–14.15 med klubba</p><p>Jullovet</p><p>Måndag 10.00–11.00 med klubba</p>"), TODAY)
    assert any("Jullovet" in w for w in r.warnings)


def test_layout_change_fails_loudly():
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><h3>Ishallar</h3></body></html>"}, TODAY)
    with pytest.raises(ParseError):
        a.parse(_page("<p>Kontakta oss.</p>"), TODAY)


def test_week_range_heading_is_a_weekly_template():
    """The page from 27 Nov 2026: Hägernäs lists "Vecka 46 - Vecka 51" with one line per weekday."""
    raw = {"page": (FIXTURES / "taby_web_week_range.html").read_text(encoding="utf-8")}
    r = a.parse(raw, date(2026, 11, 27))
    assert r.warnings == []
    assert (r.halls[HAG].covers_from, r.halls[HAG].covers_to) == ("2026-11-27", "2026-12-20")
    assert _s(r, HAG, "2026-11-30") == [("12:30", "14:15", "with_stick")]
    assert _s(r, HAG, "2026-12-01") == [("12:30", "14:15", "no_stick")]
    assert _s(r, HAG, "2026-12-19") == [("13:30", "15:15", "no_stick")]
    assert _s(r, HAG, "2026-12-20") == [("13:30", "15:15", "with_stick")]
    assert len(r.halls[HAG].sessions) == 24  # Fri 27 Nov to Sun 20 Dec, one a day
    assert _s(r, TIB, "2026-11-30") == [("15:00", "15:45", "no_stick")]


def test_several_week_ranges_and_a_dated_week_override():
    r = a.parse(_page("<p>Vecka 45 - Vecka 46</p><p>Måndag 12.30–14.15 med klubba</p>"
                      "<p>Vecka 47–48</p><p>Måndag 10.00–11.00 utan klubba</p>"
                      "<p>Vecka 47</p><p>Måndag Stängd</p>"), TODAY)
    assert r.warnings == []
    assert _s(r, HAG, "2026-11-02") == _s(r, HAG, "2026-11-09") == [("12:30", "14:15", "with_stick")]
    assert _s(r, HAG, "2026-11-16") == []  # week 47: the dated block says closed
    assert _s(r, HAG, "2026-11-23") == [("10:00", "11:00", "no_stick")]
    assert r.halls[HAG].covers_to == "2026-11-29"


def test_overlapping_week_ranges_fail_loudly():
    with pytest.raises(ParseError):
        a.parse(_page("<p>Vecka 45 - Vecka 47</p><p>Måndag 12.30–14.15 med klubba</p>"
                      "<p>Vecka 47 - Vecka 48</p><p>Måndag 10.00–11.00 utan klubba</p>"), TODAY)
