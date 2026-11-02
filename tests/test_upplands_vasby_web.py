from datetime import date

import pytest

from adapters import upplands_vasby_web as a
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 2)
HID = "upplands-vasby-vilundaparkens-ishall-b"


@pytest.fixture(scope="module")
def result():
    raw = {"page": (FIXTURES / "upplands_vasby_web.html").read_text(encoding="utf-8")}
    return a.parse(raw, TODAY)


def test_covers_and_updated(result):
    assert (result.covers_from, result.covers_to) == ("2026-11-02", "2026-11-29")
    assert result.source_updated == "2026-10-27T00:00"
    assert result.warnings == []


def test_one_hall(result):
    assert list(result.halls) == [HID]
    assert result.halls[HID].rink == "Ishall B"
    assert len(result.halls[HID].sessions) == 28  # 7 slots x 4 weeks


def _day(result, d):
    return [(x.start, x.end, x.stick) for x in result.halls[HID].sessions if x.date == d]


def test_specific_sessions(result):
    assert _day(result, "2026-11-02") == [("14:00", "15:45", "with_stick")]   # Monday
    assert _day(result, "2026-11-03") == [("12:00", "15:45", "no_stick")]     # Tuesday
    assert _day(result, "2026-11-07") == [("11:00", "13:45", "no_stick"),     # Saturday x2
                                          ("18:30", "20:45", "with_stick")]
    assert _day(result, "2026-11-29") == [("11:00", "13:45", "no_stick")]     # Sunday


def test_friday_closed(result):
    assert _day(result, "2026-11-06") == []
    assert _day(result, "2026-11-13") == []


def _page(caption: str, rows: str, extra: str = "") -> dict:
    return {"page": f"""<html><body><main><table><caption>{caption}</caption>
    <thead><tr><th>Dag</th><th>Tid</th><th>Klubba</th></tr></thead>
    <tbody>{rows}</tbody></table>{extra}</main></body></html>"""}


def test_season_start_in_future_and_end_week():
    r = a.parse(_page("Allmänhetens åkning (Ishall B), från vecka 46 till vecka 47",
                      "<tr><td>Måndag</td><td>14.00-15.45</td><td>Klubba tillåten</td></tr>"), TODAY)
    assert (r.covers_from, r.covers_to) == ("2026-11-09", "2026-11-22")
    assert [x.date for x in r.halls[HID].sessions] == ["2026-11-09", "2026-11-16"]


def test_unknown_stick_and_bad_time_and_other_rink():
    r = a.parse(_page("Allmänhetens åkning (Ishall A), från vecka 44",
                      "<tr><td>Måndag</td><td>14.00-15.45</td><td>Puckfri zon</td></tr>"
                      "<tr><td>Tisdag</td><td>15-30-17.50</td><td>Klubba tillåten</td></tr>"
                      "<tr><td>Onsdag</td><td>Se anslag</td><td></td></tr>"), TODAY)
    s = r.halls["upplands-vasby-vilundaparkens-ishall-a"].sessions
    assert {(x.stick, x.note) for x in s} == {("unknown", "Puckfri zon")}
    assert len(r.warnings) == 2


def test_extra_text_with_closure_is_a_warning():
    r = a.parse(_page("Allmänhetens åkning (Ishall B), från vecka 44",
                      "<tr><td>Måndag</td><td>14.00-15.45</td><td>Klubba tillåten</td></tr>",
                      "<p>Julafton och juldagen stängt.</p><table><tr><td>Jullov</td></tr></table>"), TODAY)
    assert len(r.warnings) == 2


def test_unknown_period_fails():
    with pytest.raises(ParseError):
        a.parse(_page("Allmänhetens åkning (Ishall B), 2–29 november",
                      "<tr><td>Måndag</td><td>14.00-15.45</td><td>Klubba tillåten</td></tr>"), TODAY)


def test_layout_change_fails_loudly():
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><main><p>Ny design!</p></main></body></html>"}, TODAY)
    with pytest.raises(ParseError):  # headers changed
        a.parse({"page": """<main><table><caption>Allmänhetens åkning (Ishall B)</caption>
            <thead><tr><th>Dag</th><th>Tid</th></tr></thead><tbody><tr><td>Måndag</td><td>14-15</td></tr></tbody>
            </table></main>"""}, TODAY)
