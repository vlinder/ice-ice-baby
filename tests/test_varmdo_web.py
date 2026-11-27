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


def test_redesigned_page_of_november_2026():
    """The page from 27 Nov 2026: plain "Åkning på …" sections, a status list such as
    "Bandyplan (utan tak): Öppen", a page-wide stick rule, "Uppdaterat senast: …",
    "Isvård sker måndag-fredag klockan 11:30-12.00" and "(isvård …)" on a day line."""
    raw = {"page": (FIXTURES / "varmdo_web_2026_11.html").read_text(encoding="utf-8")}
    r = a.parse(raw, date(2026, 11, 27))
    assert r.warnings == [] and r.empty_reason is None
    assert r.source_updated == "2026-11-23T00:00"
    assert set(r.halls) == {HID, BID}

    def day(hid, d):
        return [(s.start, s.end, s.stick, s.note) for s in r.halls[hid].sessions if s.date == d]
    # weekday upkeep 11:30-12:00 is cut out; "halva planen med klubba" makes every time shared
    assert day(HID, "2026-11-27") == [("08:30", "11:30", "shared", "isvård 11:30–12:00"),
                                      ("12:00", "15:00", "shared", None)]
    assert day(HID, "2026-11-28") == [("19:15", "22:00", "shared", None)]
    # "Lördag: 08.30-9.30 och 11.30-22.00 (isvård 13.30-14.30 och 17.30-18.00)"
    assert day(BID, "2026-11-28") == [
        ("08:30", "09:30", "shared", None),
        ("11:30", "13:30", "shared", "isvård 13:30–14:30"),
        ("14:30", "17:30", "shared", "isvård 17:30–18:00"),
        ("18:00", "22:00", "shared", None)]
    # "Måndag: 09.00-15.30 OBS 23/11 ingen åkning pga isvård": 23/11 is past, 30/11 is normal
    assert day(BID, "2026-11-30")[0][:2] == ("09:00", "11:30")


def _new_page(bandy_lines: str, status: str = "<li>Bandyplan (utan tak): Öppen</li>"
              "<li>Ekrinken (med tak): Öppen</li>") -> dict:
    return {"page": f"""<html><body><main><h1>Allmänhetens åkning</h1>
    <div><h2>Säsongen 2026/2027</h2><ul>{status}<li>Ekhallen: Ingen allmän åkning hålls inne i ishallen</li></ul>
    <h3>Det gäller alltid under allmänhetens åkning</h3><ul><li>Spel med klubba tillåts på halva planen enligt skyltning.</li></ul></div>
    <div><h2>Åkning på bandyplanen</h2><p>Uppdaterat senast: 2026-11-23</p><h3>Ordinarie öppettider</h3>
    {bandy_lines}</div>
    <div><h2>Åkning på Ekrinken</h2><p>Uppdaterat senast: 2026-11-20</p><h3>Ordinarie öppettider</h3>
    <p>Måndag: 08.30-15.00</p></div></main></body></html>"""}


def test_future_obs_date_cancels_that_day_only():
    r = a.parse(_new_page("<p>Måndag: 09.00-15.30 OBS 9/11 ingen åkning pga isvård</p>"), SYN)
    assert r.warnings == []
    dates = [s.date for s in r.halls[BID].sessions]
    assert "2026-11-02" in dates and "2026-11-09" not in dates and "2026-11-16" in dates


def test_new_status_list_closed_bandy_field():
    r = a.parse(_new_page("<p>Måndag: 09.00-15.30</p>",
                          status="<li>Bandyplan (utan tak): Stängd</li><li>Ekrinken (med tak): Öppen</li>"), SYN)
    assert r.halls[BID].sessions == [] and r.halls[HID].sessions
    assert any("Bandyplanen is marked closed" in w for w in r.warnings)
