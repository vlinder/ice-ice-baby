from datetime import date

import pytest

from adapters import solna_web as a
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 2)
H1 = "solna-ulriksdals-ip-hall-1"
H2 = "solna-ulriksdals-ip-hall-2"


def _raw():
    return {"page": (FIXTURES / "solna_web.html").read_text(encoding="utf-8")}


@pytest.fixture(scope="module")
def result():
    return a.parse(_raw(), TODAY)


def _s(result, hid, day):
    return [(x.start, x.end, x.stick) for x in result.halls[hid].sessions if x.date == day]


def test_halls_and_meta(result):
    # Only Hall 2 is in the weekly schedule; outdoor rinks have no times.
    assert set(result.halls) == {H2}
    assert result.halls[H2].name == "Ulriksdals IP" and result.halls[H2].rink == "Hall 2"
    assert result.source_updated == "2026-10-30T00:00"
    assert result.warnings == []


def test_covers_recurring_window(result):
    assert (result.covers_from, result.covers_to) == ("2026-11-02", "2026-11-29")


def test_weekly_sessions(result):
    assert _s(result, H2, "2026-11-02") == []
    assert _s(result, H2, "2026-11-04") == [("12:00", "13:30", "shared")]
    assert _s(result, H2, "2026-11-07") == [("08:00", "09:00", "no_stick"), ("14:30", "15:30", "with_stick")]
    assert _s(result, H2, "2026-11-29") == [("08:00", "09:30", "no_stick"), ("09:30", "10:30", "with_stick")]
    assert len(result.halls[H2].sessions) == 24


def test_holiday_block_overrides_before_season_start():
    r = a.parse(_raw(), date(2026, 10, 28))
    assert r.covers_from == "2026-10-28"
    assert _s(r, H1, "2026-10-28") == [("12:00", "13:30", "shared")]
    assert _s(r, H2, "2026-10-31") == [("08:00", "10:30", "no_stick"), ("14:30", "15:30", "with_stick")]
    # Weekly template (from week 45) does not leak into the holiday week.
    assert _s(r, H2, "2026-10-30") == []
    assert _s(r, H2, "2026-11-04") == [("12:00", "13:30", "shared")]


def _page(*sections: tuple[str, str]) -> dict:
    html = "".join(f"""<section class="plusBox"><h2 class="plusBox__title">{t}
        <span class="plusBox__toggler">Visa</span></h2><div class="plusBox__body">{b}</div></section>"""
                   for t, b in sections)
    return {"page": f"<html><body><main>{html}</main></body></html>"}


WEEKLY = ("Ishallarna på Ulriksdals IP", """<p>Tiderna gäller från och med v. 45</p>
    <h4>Hall 2</h4><h4>Onsdag</h4><p>12:00 - 13:30 (halva med klubba/puck, halva utan)</p>""")


def test_future_holiday_overrides_template():
    r = a.parse(_page(WEEKLY, ("Ulriksdals IP - Jullovet", """<h4>Onsdag 11 november</h4>
        <p>Hall 1: 10.00-11.00 (utan klubba/puck)</p><h4>Fredag 13 november</h4>
        <p>Hall 2: 10.00-11.00 (med klubba/puck)</p>""")), TODAY)
    assert _s(r, H1, "2026-11-11") == [("10:00", "11:00", "no_stick")]
    assert _s(r, H2, "2026-11-11") == []          # inside the holiday span: template replaced
    assert _s(r, H2, "2026-11-18") == [("12:00", "13:30", "shared")]


def test_unreadable_time_warns():
    r = a.parse(_page(("Ishallarna på Ulriksdals IP", """<p>Tiderna gäller från och med v. 45</p>
        <h4>Hall 2</h4><h4>Onsdag</h4><p>12:00 - 13:300 (med klubba/puck)</p>""")), TODAY)
    assert r.warnings and "13:300" in r.warnings[0]


def test_outdoor_section_warns():
    r = a.parse(_page(WEEKLY, ("Bergshamra IP", "<p>Öppet 10-20</p>")), TODAY)
    assert r.warnings == ["Section 'Bergshamra IP' is not handled by the adapter"]


def test_layout_change_fails_loudly():
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><p>Ny design</p></body></html>"}, TODAY)
    with pytest.raises(ParseError):
        a.parse(_page(("Ulriksdals IP", "<p>Information kommer</p>")), TODAY)
