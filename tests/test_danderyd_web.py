from datetime import date

import pytest

from adapters import danderyd_web as a
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 10)  # a Tuesday
EN, ST = "danderyd-enebybergs-ip-ishallen", "danderyd-stockhagens-ip-ishallen"
DV = "danderyd-danderydsvallen-uterinken"
STU = "danderyd-stockhagens-ip-uterinken"
FRI = "danderyd-danderydsvallen-frimarket"
WEATHER = "med reservation för ändring på grund av vädret"


@pytest.fixture(scope="module")
def raw():
    return {"page": (FIXTURES / "danderyd_web.html").read_text(encoding="utf-8")}


@pytest.fixture(scope="module")
def raw_hostlov():
    """The page as of late Oct 2026: höstlov list, a cancelled Wednesday, outdoor rinks without times."""
    return {"page": (FIXTURES / "danderyd_web_hostlov.html").read_text(encoding="utf-8")}


@pytest.fixture(scope="module")
def result(raw):
    return a.parse(raw, TODAY)


def _day(r, hid, d):
    return [(s.start, s.end, s.stick) for s in r.halls[hid].sessions if s.date == d]


def test_halls_counts_covers(result):
    assert {k: len(v.sessions) for k, v in result.halls.items()} == {EN: 10, ST: 8, DV: 27}
    assert (result.covers_from, result.covers_to) == ("2026-11-10", "2026-12-07")
    assert result.source_updated == "2026-11-11T00:00"
    # Neither the Stockhagen outdoor rink's prose ("vecka 49", "vecka 7-8 2027") nor the
    # Enebyberg competition weekend nor Danderydsvallen's times are warnings any more.
    assert result.warnings == [] and result.empty_reason is None


def test_specific_sessions(result):
    assert _day(result, EN, "2026-11-11") == [("10:00", "12:50", "with_stick")]
    assert _day(result, EN, "2026-11-21") == [("13:00", "14:50", "with_stick")]
    assert _day(result, EN, "2026-11-22") == [("13:00", "14:50", "no_stick")]
    assert _day(result, ST, "2026-11-14") == [("12:00", "13:50", "no_stick")]
    assert _day(result, ST, "2026-11-15") == [("09:00", "10:50", "with_stick")]
    assert _day(result, ST, "2026-11-11") == []


def test_no_skating_weekend(result):
    # "Obs! Ingen åkning i ishallen på Enebybergs IP helgen 14-15/11 på grund av konståkningstävling."
    assert _day(result, EN, "2026-11-14") == []
    assert _day(result, EN, "2026-11-15") == []
    assert _day(result, ST, "2026-11-14") == [("12:00", "13:50", "no_stick")]  # other hall unaffected


def test_danderydsvallen_outdoor_rink(result):
    assert result.halls[DV].rink == "Uterinken"
    assert _day(result, DV, "2026-11-13") == []  # "fr.om. 14/11"
    assert _day(result, DV, "2026-11-14") == [("14:30", "18:00", "unknown")]
    assert _day(result, DV, "2026-11-16") == [("09:00", "15:30", "unknown")]  # Mon in the weekday list
    assert _day(result, DV, "2026-11-18") == [("12:00", "20:00", "unknown")]
    assert _day(result, DV, "2026-11-20") == [("09:00", "15:30", "unknown"), ("19:00", "20:30", "unknown")]
    assert {s.note for s in result.halls[DV].sessions} == {WEATHER}


def test_outdoor_rinks_without_times_not_emitted(raw_hostlov):
    r = a.parse(raw_hostlov, date(2026, 11, 2))
    assert set(r.halls) == {EN, ST}
    assert r.warnings == []


def test_holiday_week_and_cancellation(raw_hostlov):
    # During höstlovet the dated list replaces the weekly times; 28/10 has a typo in
    # Enebyberg ("15-30-17.50"), which is warned about and leaves the day empty.
    r = a.parse(raw_hostlov, date(2026, 10, 26))
    assert _day(r, EN, "2026-10-26") == [("15:30", "17:50", "no_stick")]
    assert _day(r, EN, "2026-10-31") == [("13:00", "14:50", "with_stick")]
    assert _day(r, ST, "2026-11-01") == [("09:00", "10:50", "with_stick")]
    assert _day(r, EN, "2026-10-28") == []
    assert len(r.warnings) == 1 and "15-30-17.50" in r.warnings[0]
    assert _day(r, EN, "2026-11-04") == [("10:00", "12:50", "with_stick")]


def test_cancelled_weekly_date(raw_hostlov):
    r = a.parse(raw_hostlov, date(2026, 10, 21))
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


def test_times_for_unknown_outdoor_rink_are_warned():
    r = a.parse(_page(st="<p>Lördag 12-13.50, utan klubba</p><h3>Mörby IP - uterinken</h3>"
                         "<p>Isrinken beräknas öppna vecka 49.</p><p>Lördagar 10-12</p>"), TODAY)
    assert len(r.warnings) == 1 and "not parsed" in r.warnings[0]
    assert ST in r.halls and len(r.halls[ST].sessions) == 4


def test_stockhagen_outdoor_rink_with_and_without_times():
    r = a.parse(_page(st="<p>Lördag 12-13.50, utan klubba</p><h3>Stockhagens IP - uterinken</h3>"
                         "<p>Isrinken beräknas öppna vecka 49.</p><p>Lördagar 10-12</p>"), TODAY)
    assert r.warnings == []
    assert _day(r, STU, "2026-11-14") == [("10:00", "12:00", "unknown")]
    r = a.parse(_page(st="<p>Lördag 12-13.50, utan klubba</p><h3>Stockhagens IP - uterinken</h3>"
                         "<p>Isrinken beräknas öppna vecka 49.</p>"), TODAY)
    assert STU not in r.halls


def test_page_with_outdoor_rinks_open():
    """The page from 27 Nov 2026: new season sentence, <h4> sub-headings for the two rinks
    at Danderydsvallen, and times for Stockhagens IP's outdoor rink."""
    r = a.parse({"page": (FIXTURES / "danderyd_web_uteisar.html").read_text(encoding="utf-8")}, date(2026, 11, 27))
    assert r.warnings == [] and r.empty_reason is None
    assert (r.covers_from, r.covers_to) == ("2026-11-27", "2026-12-24")
    assert set(r.halls) == {EN, ST, DV, STU, FRI}
    assert _day(r, EN, "2026-12-02") == [("10:00", "12:50", "with_stick")]
    assert _day(r, ST, "2026-11-28") == [("12:00", "13:50", "no_stick")]
    # "Öppettider uterinken - med klubba": lines without their own wording get the heading's
    assert _day(r, DV, "2026-11-27") == [("09:00", "15:30", "with_stick"), ("19:00", "20:30", "with_stick")]
    assert _day(r, DV, "2026-12-02") == [("12:00", "19:40", "with_stick")]
    # "Öppettider Frimärket - utan klubba" switches to the Frimärket rink
    assert _day(r, FRI, "2026-11-27") == [("09:00", "21:00", "no_stick")]
    assert _day(r, FRI, "2026-11-28") == [("09:00", "18:00", "no_stick")]
    assert _day(r, STU, "2026-11-29") == [("12:00", "14:30", "unknown")]
    assert _day(r, STU, "2026-11-30") == []


def test_season_between_weeks_sentence():
    page = _page()["page"]
    import re
    page = re.sub(r"<h2>Nu startar[^<]*</h2>\s*<p>[^<]*</p>",
                  "<h2>Skridskosäsongen 2026/2027 är igång!</h2>"
                  "<p>Skridskosäsongen i våra ishallar varar vanligen mellan vecka 40 och 12.</p>", page)
    assert "mellan vecka 40 och 12" in page
    r = a.parse({"page": page}, date(2027, 3, 20))
    assert r.covers_to == "2027-03-28" and r.empty_reason is None


def test_unreadable_no_skating_remark_warns():
    r = a.parse(_page(st="<p>Lördag 12-13.50, utan klubba</p><p>Obs! Ingen åkning under tävlingshelgen.</p>"),
                TODAY)
    assert len(r.warnings) == 1 and r.warnings[0].startswith(ST + ": ")
    assert len(r.halls[ST].sessions) == 4


def test_single_no_skating_date():
    r = a.parse(_page(st="<p>Lördag 12-13.50, utan klubba</p><p>Obs! Stängt lördag 21/11.</p>"), TODAY)
    assert r.warnings == []
    assert _day(r, ST, "2026-11-21") == []
    assert _day(r, ST, "2026-11-28") == [("12:00", "13:50", "no_stick")]


def test_weekday_typo_is_not_taken_for_prose():
    r = a.parse(_page(st="<p>Lördag 12-13.50, utan klubba</p><p>Söndagar 9-10-50, med klubba</p>"), TODAY)
    assert len(r.warnings) == 1 and "9-10-50" in r.warnings[0]


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
