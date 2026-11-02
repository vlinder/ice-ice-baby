from datetime import date

import pytest

from adapters import varmdo_web as a
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 2)
HID = "varmdo-ekvallens-ip-ekrinken"


@pytest.fixture(scope="module")
def result():
    raw = {"page": (FIXTURES / "varmdo_web.html").read_text(encoding="utf-8")}
    return a.parse(raw, TODAY)


def test_covers_and_updated(result):
    assert (result.covers_from, result.covers_to) == ("2026-11-02", "2026-11-29")
    assert result.source_updated == "2026-10-19T00:00"
    assert result.warnings == []  # past höstlov link and closed Bandyplanen/Ishallen are not warnings
    assert result.empty_reason is None


def test_only_ekrinken(result):
    assert list(result.halls) == [HID]
    assert len(result.halls[HID].sessions) == 4 * 6  # Mon-Sat, Sunday closed


def _day(result, d):
    return [(x.start, x.end, x.stick, x.note) for x in result.halls[HID].sessions if x.date == d]


def test_specific_sessions(result):
    assert _day(result, "2026-11-02") == [("08:30", "15:00", "shared", "isvård 11:30–12:00")]
    assert _day(result, "2026-11-07") == [("19:15", "22:00", "shared", None)]
    assert _day(result, "2026-11-08") == []  # "Söndag: -"


def _page(times: str, intro: str = "Ekrinken: Öppen för säsongen") -> dict:
    return {"page": f"""<html><body><main><h1>Allmänhetens åkning</h1><p>{intro}</p>
    <h2><a aria-controls="s1"><span>Tider Ekrinken (uterinken)</span></a></h2>
    <div id="s1"><p>{times}</p></div></main></body></html>"""}


def test_no_default_stick_and_own_wording():
    r = a.parse(_page("Måndag: 08:30–15:00<br>Tisdag: 10:00–11:00 utan klubba"), TODAY)
    s = r.halls[HID].sessions
    assert {(x.date, x.stick) for x in s if x.date <= "2026-11-03"} == {
        ("2026-11-02", "unknown"), ("2026-11-03", "no_stick")}


def test_unreadable_lines_warn():
    r = a.parse(_page("Måndag: 13.300–15.15<br>Tisdag: se anslag<br>Onsdag: 08:30-15:00"), TODAY)
    assert len(r.warnings) == 2
    assert {x.date[:10] for x in r.halls[HID].sessions} >= {"2026-11-04"}


def test_future_holiday_link_and_opened_hall_warn():
    r = a.parse(_page("Måndag: 08:30-15:00",
                      intro="<a href='x'>Tider jullovet 2026</a><br>Ishallen: Öppen lördagar"), TODAY)
    assert len(r.warnings) == 2


def test_rink_closed_is_empty():
    r = a.parse(_page("Söndag: -", intro="Ekrinken: Stängd för säsongen 2026/2027"), TODAY)
    assert r.halls[HID].sessions == []
    assert r.empty_reason
    assert (r.covers_from, r.covers_to) == ("2026-11-02", "2026-11-29")


def test_layout_change_fails_loudly():
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><main><p>Ny design!</p></main></body></html>"}, TODAY)
    with pytest.raises(ParseError):  # section without weekday lines
        a.parse(_page("Tiderna kommer snart"), TODAY)
