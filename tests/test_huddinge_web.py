from datetime import date

import pytest

from adapters import huddinge_web as a
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 2)


@pytest.fixture(scope="module")
def result():
    raw = {"page": (FIXTURES / "huddinge_web.html").read_text(encoding="utf-8")}
    return a.parse(raw, TODAY)


def _day(result, hid, d):
    return [(s.start, s.end, s.stick) for s in result.halls[hid].sessions if s.date == d]


def test_halls_and_counts(result):
    assert {k: len(v.sessions) for k, v in result.halls.items()} == {
        "huddinge-bjorkangshallen-b": 27,
        "huddinge-segeltorpshallen": 24,
        "huddinge-stortorpshallen": 27,
        "huddinge-visattrahallen": 19,
        "huddinge-visattra-bandyplan": 0,
    }
    assert result.halls["huddinge-bjorkangshallen-b"].rink == "B"


def test_covers_and_updated(result):
    assert (result.covers_from, result.covers_to) == ("2026-11-02", "2026-11-29")
    assert result.source_updated == "2026-10-22T00:00"
    assert result.empty_reason is None


def test_typo_is_warned_and_skipped(result):
    assert len(result.warnings) == 1 and "11-00–12.50" in result.warnings[0]
    assert _day(result, "huddinge-visattrahallen", "2026-11-09") == []  # Monday


def test_specific_sessions(result):
    assert _day(result, "huddinge-bjorkangshallen-b", "2026-11-02") == [("12:15", "14:45", "with_stick")]
    assert _day(result, "huddinge-bjorkangshallen-b", "2026-11-03") == [("10:45", "13:15", "no_stick")]
    assert _day(result, "huddinge-segeltorpshallen", "2026-11-08") == [("11:00", "12:50", "no_stick")]
    assert _day(result, "huddinge-segeltorpshallen", "2026-11-07") == []  # Lördag: Stängt
    assert _day(result, "huddinge-visattrahallen", "2026-11-07") == [("11:00", "12:50", "with_stick")]


def test_two_times_in_one_cell(result):
    assert _day(result, "huddinge-stortorpshallen", "2026-11-04") == [
        ("13:00", "14:00", "no_stick"), ("14:00", "14:50", "with_stick")]


def test_closed_dates_from_avvikande(result):
    for hid in ("huddinge-bjorkangshallen-b", "huddinge-stortorpshallen", "huddinge-visattrahallen"):
        assert _day(result, hid, "2026-11-10") == []
        assert _day(result, hid, "2026-11-17") != []
    assert _day(result, "huddinge-segeltorpshallen", "2026-11-10") == [("11:00", "12:50", "with_stick")]


def test_season_end_cuts_window():
    raw = {"page": (FIXTURES / "huddinge_web.html").read_text(encoding="utf-8")}
    r = a.parse(raw, date(2027, 3, 15))
    assert (r.covers_from, r.covers_to) == ("2027-03-15", "2027-03-28")  # end of week 12
    r = a.parse(raw, date(2027, 4, 1))
    assert r.empty_reason and all(not h.sessions for h in r.halls.values())


ORD_TABLE = ('<div class="sv-text-portlet-content"><p>Öppet från vecka 34 2026 till vecka 12 2027.</p>'
             '<table><caption>Ordinarie tider till och med vecka 12 2027</caption><tbody>'
             '<tr><td><p>Måndag</p></td><td><p>13.00–14.50</p></td><td><p>Utan puck/boll</p></td></tr>'
             '</tbody></table></div>')


def _page(sections: dict | None = None) -> dict:
    def sec(n, title, inner):
        return (f'<div class="sv-collapsible-content"><h3><a role="button" data-env-collapse '
                f'aria-controls="s{n}"><span>{title}</span></a></h3><div id="s{n}">{inner}</div></div>')
    content = {
        "Björkängshallen B, Stuvsta": ORD_TABLE,
        "Segeltorpshallen, Segeltorp": ORD_TABLE,
        "Stortorpshallen, Trångsund": ORD_TABLE,
        "Visättrahallen och Visättra bandyplan, Flemingsberg":
            "<h3>Visättrahallen</h3>" + ORD_TABLE + "<h3>Visättra bandyplan</h3><p>Stängd för säsongen.</p>",
    }
    content.update(sections or {})
    body = "".join(sec(n, t, i) for n, (t, i) in enumerate(content.items()))
    return {"page": f"<html><body>{body}<p>Uppdaterad: 1 november 2026</p></body></html>"}


def test_minimal_page_parses():
    r = a.parse(_page(), TODAY)
    assert r.warnings == [] and r.source_updated == "2026-11-01T00:00"
    assert [s.date for s in r.halls["huddinge-segeltorpshallen"].sessions] == [
        "2026-11-02", "2026-11-09", "2026-11-16", "2026-11-23"]


def test_future_holiday_table_replaces_weekly_times():
    hol = ('<div class="sv-text-portlet-content"><p>Öppet vecka 46 2026 (testlov)</p>'
           '<table><caption>Testlov vecka 46, 9-15 november 2026</caption><tbody>'
           '<tr><td><p>Måndag</p></td><td><p>09.00–10.00</p></td><td><p>Med puck/boll</p></td></tr>'
           '<tr><td><p>Tisdag</p></td><td><p>Stängt</p></td><td><p>Stängt</p></td></tr>'
           '</tbody></table></div>')
    r = a.parse(_page({"Segeltorpshallen, Segeltorp": hol + ORD_TABLE}), TODAY)
    s = [(x.date, x.start, x.stick) for x in r.halls["huddinge-segeltorpshallen"].sessions]
    assert s == [("2026-11-02", "13:00", "no_stick"), ("2026-11-09", "09:00", "with_stick"),
                 ("2026-11-16", "13:00", "no_stick"), ("2026-11-23", "13:00", "no_stick")]


def test_exception_with_times_and_unknown_exception():
    exc = ('<h3>Avvikande öppettider</h3><ul><li>9 november: 10.00–11.00 utan klubba</li>'
           '<li>16 november: Se anslag i hallen.</li><li>23 november: Hallen är stängd.</li></ul>')
    r = a.parse(_page({"Stortorpshallen, Trångsund": exc + ORD_TABLE}), TODAY)
    s = {x.date: (x.start, x.stick) for x in r.halls["huddinge-stortorpshallen"].sessions}
    assert s == {"2026-11-02": ("13:00", "no_stick"), "2026-11-09": ("10:00", "no_stick"),
                 "2026-11-16": ("13:00", "no_stick")}  # 16/11 kept but warned, 23/11 closed
    assert len(r.warnings) == 1 and "Se anslag" in r.warnings[0]


def test_missing_section_is_layout_change():
    raw = _page()
    raw["page"] = raw["page"].replace("Segeltorpshallen, Segeltorp", "Nya hallen")
    with pytest.raises(ParseError):
        a.parse(raw, TODAY)


def test_bandy_rink_without_closed_notice_is_layout_change():
    raw = _page()
    raw["page"] = raw["page"].replace("Stängd för säsongen.", "Öppet när vädret tillåter.")
    with pytest.raises(ParseError):
        a.parse(raw, TODAY)


def test_unknown_table_is_layout_change():
    raw = _page()
    raw["page"] = raw["page"].replace("Ordinarie tider till och med vecka 12 2027", "Tider", 1)
    raw["page"] = raw["page"].replace("Öppet från vecka 34 2026 till vecka 12 2027.", "Välkommen!", 1)
    with pytest.raises(ParseError):
        a.parse(raw, TODAY)


def test_garbage_page():
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><p>Ny design!</p></body></html>"}, TODAY)
