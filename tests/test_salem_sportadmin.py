from datetime import date

import pytest

from adapters import salem_sportadmin as a
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 2)


@pytest.fixture(scope="module")
def result():
    raw = {"page": (FIXTURES / "salem_sportadmin.html").read_text(encoding="utf-8")}
    return a.parse(raw, TODAY)


def test_covers_today_to_last_listed_date(result):
    assert (result.covers_from, result.covers_to) == ("2026-11-02", "2026-12-04")
    assert result.source_updated is None
    assert result.warnings == []
    assert result.empty_reason is None


def test_one_hall(result):
    assert list(result.halls) == ["salem-salems-ishall"]


def test_sessions_are_shared_ice_and_past_dates_dropped(result):
    s = result.halls["salem-salems-ishall"].sessions
    assert [(x.date, x.start, x.end, x.stick) for x in s] == [
        ("2026-11-06", "13:00", "16:50", "shared"),
        ("2026-11-13", "13:00", "16:50", "shared"),
        ("2026-11-20", "13:00", "16:50", "shared"),
        ("2026-11-27", "13:00", "16:50", "shared"),
        ("2026-12-04", "13:00", "16:50", "shared"),
    ]
    assert all(x.source_url == a.SOURCE_URL for x in s)


def test_past_dates_kept_when_today_is_earlier():
    raw = {"page": (FIXTURES / "salem_sportadmin.html").read_text(encoding="utf-8")}
    r = a.parse(raw, date(2026, 10, 31))
    s = [(x.date, x.start, x.end) for x in r.halls["salem-salems-ishall"].sessions if x.date <= "2026-11-01"]
    assert s == [("2026-10-31", "09:30", "12:20"), ("2026-10-31", "18:00", "19:50"),
                 ("2026-11-01", "18:30", "19:50")]
    assert r.covers_from == "2026-10-31"


def _page(body: str) -> dict:
    return {"page": f"""<html><body><article class="sa-paragraph"><div>
    <h2 class="sa-paragraph__title">Allmänhetensåkning</h2>
    <div class="sa-paragraph__body">{body}</div></div></article></body></html>"""}


def test_own_wording_cancelled_day_and_duplicates():
    r = a.parse(_page("""<ul><li>6/11 13.00-16.50</li><li>6/11 13.00-16.50</li>
        <li>7/11 10.00-11.00 utan klubba</li><li>13/11 inställd</li></ul>
        <p>Delad is (halva ytan med klubba)</p>"""), TODAY)
    s = r.halls["salem-salems-ishall"].sessions
    assert [(x.date, x.start, x.stick) for x in s] == [
        ("2026-11-06", "13:00", "shared"), ("2026-11-07", "10:00", "no_stick")]
    assert r.covers_to == "2026-11-13"
    assert r.warnings == []


def test_no_wording_is_unknown_and_year_rollover():
    r = a.parse(_page("<ul><li>8/1 13.00-16.50</li></ul>"), date(2026, 12, 20))
    s = r.halls["salem-salems-ishall"].sessions
    assert [(x.date, x.stick) for x in s] == [("2027-01-08", "unknown")]


def test_typo_becomes_warning():
    r = a.parse(_page("<ul><li>6/11 13.300-16.50</li><li>13/11 13.00-16.50</li><li>se anslag 14.00</li></ul>"), TODAY)
    assert len(r.halls["salem-salems-ishall"].sessions) == 1
    assert len(r.warnings) == 2
    assert any("13.300" in w for w in r.warnings)


def test_all_past_warns():
    r = a.parse(_page("<ul><li>28/10 08.00-08.50</li></ul>"), TODAY)
    assert r.halls["salem-salems-ishall"].sessions == []
    assert (r.covers_from, r.covers_to) == ("2026-11-02", "2026-11-02")
    assert r.warnings


def test_layout_change_fails_loudly():
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><p>Ny design!</p></body></html>"}, TODAY)
    with pytest.raises(ParseError):
        a.parse(_page("<p>Tiderna kommer snart.</p>"), TODAY)


def test_explicit_no_times_is_empty():
    r = a.parse(_page("<p>Inga tider just nu.</p>"), TODAY)
    assert r.halls["salem-salems-ishall"].sessions == []
    assert r.empty_reason
    assert (r.covers_from, r.covers_to) == ("2026-11-02", "2026-11-29")
