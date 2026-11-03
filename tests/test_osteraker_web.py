from datetime import date

import pytest

from adapters import osteraker_web as a
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 2)
I1 = "osteraker-osterakers-sportcentrum-ishall-1"
I2 = "osteraker-osterakers-sportcentrum-ishall-2"


def _raw():
    return {"page": (FIXTURES / "osteraker_web.html").read_text(encoding="utf-8")}


@pytest.fixture(scope="module")
def result():
    return a.parse(_raw(), TODAY)


def _day(r, hid, d):
    return [(s.start, s.end, s.stick) for s in r.halls[hid].sessions if s.date == d]


def test_halls_and_covers(result):
    assert set(result.halls) == {I1, I2}
    assert result.halls[I1].rink == "Ishall 1"
    assert (result.covers_from, result.covers_to) == ("2026-11-02", "2026-11-29")
    assert result.source_updated == "2026-10-26T00:00"
    assert result.warnings == []
    assert result.empty_reason is None
    assert {k: len(v.sessions) for k, v in result.halls.items()} == {I1: 28, I2: 28}


def test_weekly_sessions(result):
    assert _day(result, I1, "2026-11-02") == [("14:00", "15:20", "no_stick")]
    assert _day(result, I1, "2026-11-07") == [("13:30", "14:50", "no_stick")]
    assert _day(result, I2, "2026-11-06") == [("14:10", "15:50", "with_stick")]
    assert _day(result, I2, "2026-11-08") == [("11:45", "13:00", "with_stick")]


def test_preschool_times_not_emitted(result):
    assert not [s for s in result.halls[I1].sessions if s.start == "09:00"]


def test_current_holiday_block_overrides_template():
    r = a.parse(_raw(), date(2026, 10, 28))
    assert (r.covers_from, r.covers_to) == ("2026-10-28", "2026-11-24")
    assert _day(r, I1, "2026-10-29") == [("08:00", "13:15", "no_stick")]
    assert _day(r, I1, "2026-10-31") == [("13:30", "14:50", "no_stick")]
    assert _day(r, I2, "2026-10-28") == [("16:10", "18:00", "with_stick")]
    assert _day(r, I2, "2026-10-30") == [("12:00", "14:00", "with_stick"), ("14:10", "15:50", "with_stick")]
    assert _day(r, I2, "2026-11-02") == [("14:10", "15:50", "with_stick")]
    assert r.warnings == []


def test_season_end():
    r = a.parse(_raw(), date(2027, 4, 1))
    assert (r.covers_from, r.covers_to) == ("2027-04-01", "2027-04-11")
    over = a.parse(_raw(), date(2027, 4, 20))
    assert over.empty_reason and all(not h.sessions for h in over.halls.values())


@pytest.mark.parametrize("text,expected", [
    ("måndagar–fredagar", [0, 1, 2, 3, 4]),
    ("lördagar och söndagar", [5, 6]),
    ("lördag och söndag", [5, 6]),
    ("måndag, onsdag och fredag", [0, 2, 4]),
    ("26-30 oktober", None),
])
def test_weekdays_spec(text, expected):
    assert a.weekdays_spec(text) == expected


def test_block_dates():
    b = (date(2026, 12, 21), date(2027, 1, 6))
    assert a._block_dates("28 december – 3 januari", *b)[-1] == date(2027, 1, 3)
    assert a._block_dates("2-3 januari", *b) == [date(2027, 1, 2), date(2027, 1, 3)]
    assert a._block_dates("lördag och söndag", *b)[:2] == [date(2026, 12, 26), date(2026, 12, 27)]
    assert a._block_dates("se anslag", *b) is None


JULLOV = """<h2>Jullov 21 december – 6 januari</h2>
<h3>Ishall 1 allmänhetens åkning under jullovet</h3>
<table><thead><tr><th>Dag</th><th>Tid</th><th>Övrig information</th></tr></thead><tbody>
<tr><td>21-23 december</td><td>10–12</td><td>Utan klubbor</td></tr>
<tr><td>24-26 december</td><td>stängt</td><td></td></tr>
<tr><td>28 december – 6 januari</td><td>10–12, 13-15.30</td><td>Familjeåkning</td></tr>
</tbody></table>"""


def test_future_holiday_block_and_unknown_wording():
    raw = _raw()
    raw["page"] = raw["page"].replace('<div id="Text1"><!-- Text 1 --></div>', JULLOV)
    r = a.parse(raw, date(2026, 12, 10))
    assert (r.covers_from, r.covers_to) == ("2026-12-10", "2027-01-06")
    assert _day(r, I1, "2026-12-18") == [("14:00", "15:20", "no_stick")]
    assert _day(r, I1, "2026-12-21") == [("10:00", "12:00", "no_stick")]
    assert _day(r, I1, "2026-12-25") == []  # "stängt"
    assert _day(r, I1, "2026-12-27") == []  # in the block but not listed
    s = [x for x in r.halls[I1].sessions if x.date == "2027-01-02"]
    assert [(x.start, x.end, x.stick, x.note) for x in s] == [
        ("10:00", "12:00", "unknown", "Familjeåkning"), ("13:00", "15:30", "unknown", "Familjeåkning")]
    assert _day(r, I2, "2026-12-27") == [("11:45", "13:00", "with_stick")]
    assert r.warnings == []


def test_block_only_for_one_hall_does_not_stretch_covers():
    raw = _raw()
    raw["page"] = raw["page"].replace('<div id="Text1"><!-- Text 1 --></div>', JULLOV)
    # Ishall 1's description runs to 6 January (block), Ishall 2's to 11 April (season),
    # but the 4-week window ends 3 January for both
    r = a.parse(raw, date(2026, 12, 7))
    assert (r.covers_from, r.covers_to) == ("2026-12-07", "2027-01-03")


def test_typo_becomes_warning():
    raw = _raw()
    raw["page"] = raw["page"].replace("<p>14.10–15.50</p>", "<p>14.10–15-50</p>", 1)
    r = a.parse(raw, TODAY)
    assert len(r.warnings) == 1 and "15-50" in r.warnings[0]
    assert _day(r, I2, "2026-11-02") == []
    assert _day(r, I2, "2026-11-07") == [("11:45", "13:00", "with_stick")]


def test_missing_hall_table_fails_loudly():
    raw = _raw()
    raw["page"] = raw["page"].replace("Ishall 2, 17 augusti 2026 – 11 april 2027", "Ishall två")
    with pytest.raises(ParseError):
        a.parse(raw, TODAY)


def test_layout_change_fails_loudly():
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><main><h2>Ny design</h2></main></body></html>"}, TODAY)


def test_off_season_page_is_empty_with_reason():
    page = "<html><body><main><p>Förskolan och allmänheten har tider igen i augusti 2027.</p></main></body></html>"
    r = a.parse({"page": page}, date(2027, 6, 1))
    assert r.empty_reason and set(r.halls) == {I1, I2}
    assert all(not h.sessions for h in r.halls.values())
