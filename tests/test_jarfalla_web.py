from datetime import date

import pytest

from adapters import jarfalla_web as a
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 2)
J = "jarfalla-jarfalla-ishall"
K = "jarfalla-kallhalls-ishall"


def _raw():
    return {"page": (FIXTURES / "jarfalla_web.html").read_text(encoding="utf-8")}


@pytest.fixture(scope="module")
def result():
    return a.parse(_raw(), TODAY)


def _day(result, hid, d):
    return [(s.start, s.end, s.stick) for s in result.halls[hid].sessions if s.date == d]


def test_halls_and_covers(result):
    assert set(result.halls) == {J, K}
    assert (result.covers_from, result.covers_to) == ("2026-11-02", "2026-11-29")
    assert result.warnings == []
    assert result.empty_reason is None
    assert result.source_updated is None
    assert {k: len(v.sessions) for k, v in result.halls.items()} == {J: 28, K: 28}


def test_weekly_sessions(result):
    assert _day(result, J, "2026-11-02") == [("14:00", "16:00", "with_stick")]
    assert _day(result, J, "2026-11-03") == [("14:00", "16:00", "no_stick")]
    assert _day(result, J, "2026-11-07") == [("12:00", "14:00", "with_stick")]
    assert _day(result, K, "2026-11-07") == [("11:00", "13:00", "no_stick")]
    assert _day(result, K, "2026-11-29") == [("11:00", "13:00", "with_stick")]
    assert all(s.source_url == a.SOURCE_URL for s in result.halls[J].sessions)


def test_window_cut_at_season_end():
    r = a.parse(_raw(), date(2026, 12, 10))
    assert (r.covers_from, r.covers_to) == ("2026-12-10", "2026-12-20")
    assert max(s.date for s in r.halls[J].sessions) == "2026-12-20"


def test_season_over_without_new_times_fails_loudly():
    # "Tider 21 december och framåt kommer": unknown, not closed
    with pytest.raises(ParseError):
        a.parse(_raw(), date(2026, 12, 21))


def test_holiday_block_overrides_template_while_current():
    r = a.parse(_raw(), date(2026, 10, 27))
    assert (r.covers_from, r.covers_to) == ("2026-10-27", "2026-11-23")
    # höstlov times replace the weekly template on Tuesday 27/10
    assert _day(r, J, "2026-10-27") == [("12:00", "14:55", "no_stick"), ("15:05", "18:00", "with_stick")]
    assert _day(r, K, "2026-11-01") == [("11:00", "13:00", "with_stick")]
    # and the template applies again afterwards
    assert _day(r, J, "2026-11-03") == [("14:00", "16:00", "no_stick")]
    assert r.warnings == []


def _page(body: str) -> dict:
    hall = lambda name, lines: (  # noqa: E731
        f"<h3>Allmänhetens tider - {name}</h3><p><strong>Säsongen 2026/2027 (26 oktober - 20 december)<br></strong>"
        f"{lines}</p>")
    return {"page": "<html><body><main>"
            + hall("Järfälla ishall", "Måndag 14.00-16.00 Med klubbor")
            + hall("Kallhälls ishall", "Tisdag 14.00-16.00 Utan klubbor")
            + body + "</main></body></html>"}


def test_typo_becomes_warning():
    p = _page("")
    p["page"] = p["page"].replace("Måndag 14.00-16.00 Med klubbor", "Måndag 14.00-16.00 Med klubbor<br>Onsdag 15-30-17.50 Med klubbor")
    r = a.parse(p, TODAY)
    assert len(r.warnings) == 1 and "15-30-17.50" in r.warnings[0]
    assert {s.date for s in r.halls[J].sessions} == {"2026-11-02", "2026-11-09", "2026-11-16", "2026-11-23"}


def test_unknown_stick_wording_kept_as_note():
    p = _page("")
    p["page"] = p["page"].replace("Med klubbor", "Familjeåkning")
    r = a.parse(p, TODAY)
    s = r.halls[J].sessions[0]
    assert (s.stick, s.note) == ("unknown", "Familjeåkning")


def _jullov(hall):
    return (f"<h3><strong>Allmänhetens åkning {hall} Jullovet Vecka 52 (21/12 - 27/12)</strong></h3>"
            "<p><strong>Måndag 21/12</strong><br>12.00–14.55 Med klubba<br>15.05–18.00 Utan klubba</p>")


def test_future_jullov_block_extends_covers():
    r = a.parse(_page(_jullov("Järfälla ishall") + _jullov("Kallhäll ishall")), date(2026, 12, 10))
    assert (r.covers_from, r.covers_to) == ("2026-12-10", "2026-12-27")
    assert _day(r, J, "2026-12-21") == [("12:00", "14:55", "with_stick"), ("15:05", "18:00", "no_stick")]
    assert _day(r, K, "2026-12-22") == []  # block lists only Monday: no skating on Tuesday


def test_covers_only_what_every_hall_describes():
    # only Järfälla has a jullov block; Kallhäll says nothing after 20 December
    r = a.parse(_page(_jullov("Järfälla ishall")), date(2026, 12, 10))
    assert (r.covers_from, r.covers_to) == ("2026-12-10", "2026-12-20")
    assert max(s.date for s in r.halls[J].sessions) <= "2026-12-20"


def test_missing_hall_heading_fails_loudly():
    raw = _raw()
    raw["page"] = raw["page"].replace("Allmänhetens tider - Kallhälls ishall", "Kallhälls ishall")
    with pytest.raises(ParseError):
        a.parse(raw, TODAY)


def test_missing_season_line_fails_loudly():
    raw = _raw()
    raw["page"] = raw["page"].replace("Säsongen 2026/2027", "Hösten")
    with pytest.raises(ParseError):
        a.parse(raw, TODAY)


def test_layout_change_fails_loudly():
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><main><p>Ny design!</p></main></body></html>"}, TODAY)
