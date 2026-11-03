from datetime import date

import pytest

from adapters import danderyd_web as a
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 2)
EN, ST = "danderyd-enebybergs-ip-ishallen", "danderyd-stockhagens-ip-ishallen"


@pytest.fixture(scope="module")
def raw():
    return {"page": (FIXTURES / "danderyd_web.html").read_text(encoding="utf-8")}


@pytest.fixture(scope="module")
def result(raw):
    return a.parse(raw, TODAY)


def _day(r, hid, d):
    return [(s.start, s.end, s.stick) for s in r.halls[hid].sessions if s.date == d]


def test_halls_counts_covers(result):
    assert {k: len(v.sessions) for k, v in result.halls.items()} == {EN: 12, ST: 8}
    assert (result.covers_from, result.covers_to) == ("2026-11-02", "2026-11-29")
    assert result.source_updated == "2026-10-28T00:00"
    assert result.warnings == [] and result.empty_reason is None


def test_specific_sessions(result):
    assert _day(result, EN, "2026-11-04") == [("10:00", "12:50", "with_stick")]
    assert _day(result, EN, "2026-11-07") == [("13:00", "14:50", "with_stick")]
    assert _day(result, EN, "2026-11-08") == [("13:00", "14:50", "no_stick")]
    assert _day(result, ST, "2026-11-07") == [("12:00", "13:50", "no_stick")]
    assert _day(result, ST, "2026-11-08") == [("09:00", "10:50", "with_stick")]
    assert _day(result, ST, "2026-11-04") == []


def test_outdoor_rinks_not_emitted(result):
    assert not any("danderydsvallen" in k for k in result.halls)


def test_holiday_week_and_cancellation(raw):
    # During höstlovet the dated list replaces the weekly times; 28/10 has a typo in
    # Enebyberg ("15-30-17.50"), which is warned about and leaves the day empty.
    r = a.parse(raw, date(2026, 10, 26))
    assert _day(r, EN, "2026-10-26") == [("15:30", "17:50", "no_stick")]
    assert _day(r, EN, "2026-10-31") == [("13:00", "14:50", "with_stick")]
    assert _day(r, ST, "2026-11-01") == [("09:00", "10:50", "with_stick")]
    assert _day(r, EN, "2026-10-28") == []
    assert len(r.warnings) == 1 and "15-30-17.50" in r.warnings[0]
    assert _day(r, EN, "2026-11-04") == [("10:00", "12:50", "with_stick")]


def test_cancelled_weekly_date(raw):
    r = a.parse(raw, date(2026, 10, 21))
    assert _day(r, EN, "2026-10-21") == [("10:00", "12:50", "with_stick")]
    assert _day(r, EN, "2026-10-28") == []  # OBS! Inställt onsd 28/10 (and an unreadable holiday line)


def test_season_bounds(raw):
    r = a.parse(raw, date(2027, 3, 15))
    assert r.covers_to == "2027-03-28"  # Sunday of week 12, 2027
    r = a.parse(raw, date(2026, 9, 20))
    assert r.covers_from == "2026-09-28"  # Monday of week 40, 2026
    r = a.parse(raw, date(2027, 4, 5))
    assert r.empty_reason and not any(h.sessions for h in r.halls.values())


PAGE = """<html><body><div class="mainbody">
<h2>Nu startar skridskosäsongen 2026/2027</h2>
<p>Vecka 40 drar skridskosäsongen igång igen i våra två ishallar. De håller sedan öppet till och med vecka 12.</p>
{extra}
<h2>Ordinarie öppettider</h2>
<h3>Stockhagens IP - ishallen</h3><p><strong>Adress:</strong> Stockhagsvägen 9</p><p><strong>Öppettider:</strong></p>
{st}
<h3>Enebybergs IP - ishallen</h3><p>Lördag 13-14.50, med klubba</p>
<h3>Danderydsvallen - uterinken</h3><p>Säsongen varar normalt mellan vecka 47 och vecka 9.</p>{dv}
</div></body></html>"""


def _page(st="<p>Lördag 12-13.50, utan klubba</p>", extra="", dv=""):
    return {"page": PAGE.format(st=st, extra=extra, dv=dv)}


def test_future_dated_list():
    extra = ("<h2>Extra öppet under jullovet vecka 52!</h2><h3>Ishallen Stockhagen IP</h3>"
             "<p><strong>Lördag 26/12</strong> kl. 1200-13.50 med klubba</p>"
             "<p><strong>Söndag 27/12</strong> Stängt</p>")
    r = a.parse(_page(st="<p>Lördag 12-13.50, utan klubba</p><p>Söndagar 9-10.50, med klubba</p>",
                      extra=extra), date(2026, 12, 20))
    assert _day(r, ST, "2026-12-26") == [("12:00", "13:50", "with_stick")]
    assert _day(r, ST, "2026-12-27") == []
    assert _day(r, ST, "2027-01-02") == [("12:00", "13:50", "no_stick")]


def test_unknown_stick_wording_and_bad_time():
    r = a.parse(_page(st="<p>Lördag 12-13.50, för familjer</p><p>Söndagar 9-10-50, med klubba</p>"), TODAY)
    assert [(s.stick, s.note) for s in r.halls[ST].sessions][:1] == [("unknown", "för familjer")]
    assert len(r.warnings) == 2


def test_times_for_outdoor_rink_are_warned():
    r = a.parse(_page(dv="<p>Lördagar 10-12</p>"), TODAY)
    assert any("not parsed" in w for w in r.warnings)


def test_missing_hall_is_layout_change():
    with pytest.raises(ParseError):
        a.parse({"page": PAGE.format(st="", extra="", dv="").replace("Stockhagens IP - ishallen", "Ny hall")}, TODAY)


def test_missing_season_is_layout_change():
    raw = _page()
    raw["page"] = raw["page"].replace("till och med vecka 12", "hela vintern")
    with pytest.raises(ParseError):
        a.parse(raw, TODAY)


def test_garbage_page():
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><p>Ny design!</p></body></html>"}, TODAY)
