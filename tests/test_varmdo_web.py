from datetime import date

import pytest

from adapters import varmdo_web as a
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 16)  # a Monday
HID = "varmdo-ekvallens-ip-ekrinken"
BID = "varmdo-ekvallens-ip-bandyplanen"
SYN = date(2026, 11, 2)  # for the hand-written pages below


@pytest.fixture(scope="module")
def result():
    raw = {"page": (FIXTURES / "varmdo_web.html").read_text(encoding="utf-8")}
    return a.parse(raw, TODAY)


def test_covers_and_updated(result):
    assert (result.covers_from, result.covers_to) == ("2026-11-16", "2026-12-13")
    assert result.source_updated == "2026-11-13T00:00"  # newest of the two sections
    assert result.warnings == []  # open Bandyplanen has its own section; closed Ishallen is not a warning
    assert result.empty_reason is None


def test_ekrinken_and_bandyplanen(result):
    assert set(result.halls) == {HID, BID}
    assert result.halls[BID].rink == "Bandyplanen"
    assert len(result.halls[HID].sessions) == 4 * 12  # Mon–Fri split by isvård
    assert len(result.halls[BID].sessions) == 4 * 8  # Fri 1, Sat 2+2 (spolning), Sun 3


def _day(result, d, hid=HID):
    return [(x.start, x.end, x.stick, x.note) for x in result.halls[hid].sessions if x.date == d]


def test_specific_sessions(result):
    assert _day(result, "2026-11-16") == [  # split around "Isvård måndag-fredag 11.30–12.00"
        ("08:30", "11:30", "shared", "isvård 11:30–12:00"), ("12:00", "15:00", "shared", None)]
    assert _day(result, "2026-11-21") == [("17:00", "22:00", "shared", None)]
    assert _day(result, "2026-11-22") == [("12:30", "14:00", "shared", None)]


def test_bandyplanen_sessions(result):
    assert _day(result, "2026-11-16", BID) == []  # "avstängd dagtid för isvård"
    assert _day(result, "2026-11-20", BID) == [("16:00", "22:00", "shared", None)]
    assert _day(result, "2026-11-21", BID) == [
        ("08:30", "09:30", "shared", None),
        ("11:30", "13:30", "shared", "spolning 13:30–14:00"),
        ("14:00", "17:30", "shared", "spolning 17:30–18:00"),
        ("18:00", "22:00", "shared", None)]
    assert _day(result, "2026-11-22", BID) == [
        ("09:00", "13:30", "shared", "spolning 13:30–14:00"),
        ("14:00", "18:30", "shared", "spolning 18:30–19:00"),
        ("19:00", "22:00", "shared", None)]


def _page(times: str, intro: str = "Ekrinken: Öppen för säsongen") -> dict:
    return {"page": f"""<html><body><main><h1>Allmänhetens åkning</h1><p>{intro}</p>
    <h2><a aria-controls="s1"><span>Tider Ekrinken (uterinken)</span></a></h2>
    <div id="s1"><p>{times}</p></div></main></body></html>"""}


def test_no_default_stick_and_own_wording():
    r = a.parse(_page("Måndag: 08:30–15:00<br>Tisdag: 10:00–11:00 utan klubba"), SYN)
    s = r.halls[HID].sessions
    assert {(x.date, x.stick) for x in s if x.date <= "2026-11-03"} == {
        ("2026-11-02", "unknown"), ("2026-11-03", "no_stick")}


def test_unreadable_lines_warn():
    r = a.parse(_page("Måndag: 13.300–15.15<br>Tisdag: se anslag<br>Onsdag: 08:30-15:00"), SYN)
    assert len(r.warnings) == 2
    assert {x.date[:10] for x in r.halls[HID].sessions} >= {"2026-11-04"}


def test_future_holiday_link_and_opened_hall_warn():
    r = a.parse(_page("Måndag: 08:30-15:00",
                      intro="<a href='x'>Tider jullovet 2026</a><br>Ishallen: Öppen lördagar"), SYN)
    assert len(r.warnings) == 2


def test_rink_closed_is_empty():
    r = a.parse(_page("Söndag: -", intro="Ekrinken: Stängd för säsongen 2026/2027"), SYN)
    assert r.halls[HID].sessions == []
    assert r.empty_reason
    assert (r.covers_from, r.covers_to) == ("2026-11-02", "2026-11-29")


def test_layout_change_fails_loudly():
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><main><p>Ny design!</p></main></body></html>"}, TODAY)
    with pytest.raises(ParseError):  # section without weekday lines
        a.parse(_page("Tiderna kommer snart"), SYN)


def _two_sections(bandy: str, intro: str = "Bandyplanen: Öppen<br>Ekrinken: Öppen") -> dict:
    return {"page": f"""<html><body><main><p>{intro}</p>
    <h2><a aria-controls="b"><span>Tider bandyplan</span></a></h2><div id="b">{bandy}</div>
    <h2><a aria-controls="e"><span>Tider Ekrinken (uterinken)</span></a></h2>
    <div id="e"><p>Måndag: 08.30–15.00</p></div></main></body></html>"""}


def test_bandy_section_warnings_name_the_hall():
    r = a.parse(_two_sections("<p>Måndag: se anslag<br>Tisdag: 10.00–12.00; spolning ibland</p>"), TODAY)
    assert len(r.warnings) == 2
    assert all(w.startswith(BID + ": ") for w in r.warnings)


def test_open_bandyplanen_without_section_still_warns():
    r = a.parse(_page("Måndag: 08:30-15:00", intro="Bandyplanen: Öppen för säsongen"), SYN)
    assert list(r.halls) == [HID]
    assert len(r.warnings) == 1


def test_closed_bandyplanen_is_empty_but_not_the_source():
    r = a.parse(_two_sections("<p>Status: Stängd för säsongen</p><p>Söndag: -</p>"), TODAY)
    assert r.halls[BID].sessions == []
    assert r.halls[HID].sessions
    assert r.empty_reason is None
    r = a.parse(_two_sections("<p>Söndag: -</p>", intro="Bandyplanen: Stängd<br>Ekrinken: Stängd"), TODAY)
    assert r.empty_reason


def test_past_hostlov_link_is_ignored():
    r = a.parse(_page("Måndag: 08:30-15:00", intro="<a href='x'>OBS klicka här för tider höstlovet 2026</a>"), SYN)
    assert r.warnings == []
