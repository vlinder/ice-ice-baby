from datetime import date

import pytest

from adapters import sollentuna_web as a
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 2)
A = "sollentuna-sollentunavallen-a-hallen"
B = "sollentuna-sollentunavallen-b-hallen"


@pytest.fixture(scope="module")
def result():
    raw = {"page": (FIXTURES / "sollentuna_web.html").read_text(encoding="utf-8")}
    return a.parse(raw, TODAY)


def _s(result, hid, day):
    return [(x.start, x.end, x.stick) for x in result.halls[hid].sessions if x.date == day]


def test_halls_and_meta(result):
    assert set(result.halls) == {A, B}
    assert result.halls[A].name == "Sollentunavallen" and result.halls[A].rink == "A-hallen"
    assert result.source_updated == "2026-10-29T00:00"
    assert result.warnings == []
    assert result.empty_reason is None


def test_covers_stops_at_last_listed_day(result):
    # Week 45 lists Monday–Friday only.
    assert (result.covers_from, result.covers_to) == ("2026-11-02", "2026-11-06")


def test_sessions(result):
    assert _s(result, B, "2026-11-02") == [("10:00", "12:30", "with_stick"), ("12:30", "14:45", "no_stick")]
    assert _s(result, B, "2026-11-05") == [("10:00", "12:30", "no_stick"), ("12:30", "14:45", "with_stick")]
    assert _s(result, A, "2026-11-06") == [("10:00", "12:00", "with_stick"), ("12:00", "14:45", "no_stick")]
    assert len(result.halls[A].sessions) == 2
    assert len(result.halls[B].sessions) == 6
    assert all(x.source_url == a.SOURCE_URL for x in result.halls[B].sessions)


def test_explicit_no_skating_day(result):
    # Tisdag: "Ingen allmänhetens åkning!"
    assert _s(result, A, "2026-11-03") == [] and _s(result, B, "2026-11-03") == []


def test_past_week_skipped(result):
    # Week 44 (höstlov) ended yesterday; its BUSSTER line must not warn.
    assert all(x.date >= "2026-11-02" for h in result.halls.values() for x in h.sessions)


def test_past_week_parsed_when_current(result):
    raw = {"page": (FIXTURES / "sollentuna_web.html").read_text(encoding="utf-8")}
    r = a.parse(raw, date(2026, 10, 29))
    assert (r.covers_from, r.covers_to) == ("2026-10-26", "2026-11-06")
    assert _s(r, A, "2026-10-26") == [("14:30", "17:30", "with_stick")]
    # Thursday: the BUSSTER block is skipped (with a warning), the public time kept.
    assert _s(r, A, "2026-10-29") == [("15:00", "17:45", "no_stick")]
    assert len(r.warnings) == 1 and "BUSSTER" in r.warnings[0]


def _page(body: str) -> dict:
    return {"page": f"""<html><body><main>
    <h2>Allmänhetens åkning på Sollentunavallen</h2>{body}
    <h2>Boka ishallarna och bandyplanen</h2><p>Ishall B kan bokas 1 oktober–8 mars.</p>
    </main></body></html>"""}


def test_typo_becomes_warning_and_unknown_wording_is_kept():
    r = a.parse(_page("""<h2>Vecka 45</h2><h3>Måndag</h3><ul>
        <li>A-hallen: klockan 15-30-17.50 - Med klubba.</li>
        <li>B-hallen: klockan 10.00-12.00 - Familjeåkning</li>
        <li>B-hallen: klockan 10.00-12.00 - Familjeåkning</li></ul>"""), TODAY)
    assert len(r.warnings) == 1 and "15-30-17.50" in r.warnings[0]
    s = r.halls[B].sessions
    assert [(x.date, x.start, x.end, x.stick, x.note) for x in s] == [
        ("2026-11-02", "10:00", "12:00", "unknown", "Familjeåkning")]
    assert (r.covers_from, r.covers_to) == ("2026-11-02", "2026-11-02")


def test_unexpected_line_warns():
    r = a.parse(_page("<h2>Vecka 45</h2><h3>Måndag</h3><ul><li>Se anslag i hallen</li></ul>"), TODAY)
    assert r.warnings and "Se anslag" in r.warnings[0]


def test_only_past_weeks_gives_past_covers():
    r = a.parse(_page("<h2>Vecka 44</h2><h3>Måndag</h3><ul><li>A-hallen: klockan 10-12 - Med klubba.</li></ul>"), TODAY)
    assert (r.covers_from, r.covers_to) == ("2026-10-26", "2026-10-26")
    assert all(not h.sessions for h in r.halls.values())


def test_layout_change_fails_loudly():
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><main><h2>Skridskor</h2></main></body></html>"}, TODAY)
    with pytest.raises(ParseError):
        a.parse(_page("<p>Tider kommer snart.</p>"), TODAY)
    with pytest.raises(ParseError):
        a.parse(_page("<h2>Vecka 45</h2><h3>Lucia</h3><ul><li>A-hallen: klockan 10-12</li></ul>"), TODAY)


# The page as revised later on 2026-11-02: the weekday list has no "Vecka" heading,
# so it is the standing weekly schedule.
def unlabelled():
    return (FIXTURES / "sollentuna_web_unlabelled.html").read_text(encoding="utf-8")


def test_weekly_schedule_is_expanded_like_other_weekly_schedules():
    r = a.parse({"page": unlabelled()}, date(2026, 11, 2))
    assert r.warnings == []
    assert (r.covers_from, r.covers_to) == ("2026-11-02", "2026-11-29")
    got = [(s.date, s.start, s.end, s.stick) for s in r.halls["sollentuna-sollentunavallen-a-hallen"].sessions]
    assert len(got) == 36  # 9 times a week, four weeks
    assert got[:2] == [("2026-11-02", "10:00", "12:30", "no_stick"), ("2026-11-02", "12:30", "14:45", "with_stick")]
    assert ("2026-11-11", "10:00", "11:30", "with_stick") in got
    assert not any(d in ("2026-11-07", "2026-11-08") for d, *_ in got)  # no weekend times listed
    assert r.halls["sollentuna-sollentunavallen-b-hallen"].sessions == []


def test_window_moves_with_today():
    r = a.parse({"page": unlabelled()}, date(2026, 11, 20))
    assert (r.covers_from, r.covers_to) == ("2026-11-20", "2026-12-17")
    assert min(s.date for s in r.halls["sollentuna-sollentunavallen-a-hallen"].sessions) == "2026-11-20"


def test_a_dated_week_replaces_the_weekly_schedule_for_that_week():
    week = ('<h2>Vecka 46:</h2><h3>Måndag</h3><ul><li>B-hallen: klockan 09.00-10.00 - Med klubba.</li></ul>'
            '<h3>Tisdag</h3><ul><li>Ingen allmänhetens åkning!</li></ul>')
    page = unlabelled().replace("<h2>Boka ishallarna", week + "<h2>Boka ishallarna", 1)
    assert page != unlabelled()
    r = a.parse({"page": page}, date(2026, 11, 2))
    a_hall = {s.date for s in r.halls["sollentuna-sollentunavallen-a-hallen"].sessions}
    b_hall = [(s.date, s.start) for s in r.halls["sollentuna-sollentunavallen-b-hallen"].sessions]
    # Monday and Tuesday of week 46 come from the dated list; the rest of the week is the weekly schedule.
    assert "2026-11-06" in a_hall and "2026-11-09" not in a_hall and "2026-11-10" not in a_hall
    assert "2026-11-11" in a_hall
    assert b_hall == [("2026-11-09", "09:00")]
