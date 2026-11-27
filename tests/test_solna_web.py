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


BERG = "solna-bergshamra-ip"
FRIM = "solna-huvudstafaltet-frimarket"
BERG_OPEN = """<p>Isbanan på Bergshamra IP är öppen.</p><p>Öppet alla dagar 10.00 – 22.00. Belysningen är på fram till kl. 22.00</p>
    <p>Spel med klubbor och puck/boll är tillåtet mellan kl. 19.00 och 22.00.</p>
    <h3>Utlåningens öppettider:</h3><p>Lördag: 10.00 - 15.00<br>Söndag: 10.00 - 15.00</p>"""


def test_outdoor_rinks_not_open_yet_on_the_live_page():
    """The page on 27 Nov 2026: both rinks "öppnar ... till jullovet", with no date."""
    r = a.parse({"page": (FIXTURES / "solna_web_outdoor.html").read_text(encoding="utf-8")}, date(2026, 11, 27))
    assert r.warnings == []
    assert {BERG, FRIM} <= set(r.halls)
    assert (r.halls[BERG].name, r.halls[BERG].rink) == ("Bergshamra IP", None)
    assert (r.halls[FRIM].name, r.halls[FRIM].rink) == ("Huvudstafältet", "Frimärket")
    assert r.halls[BERG].sessions == [] and r.halls[FRIM].sessions == []
    assert r.halls[H2].sessions  # the indoor hall is unaffected


def test_open_outdoor_rink_every_day_with_stick_hours():
    r = a.parse(_page(WEEKLY, ("Bergshamra IP", BERG_OPEN)), TODAY)
    assert r.warnings == []
    days = {x.date for x in r.halls[BERG].sessions}
    assert len(days) == 28  # every covered day; the lending hours are not sessions
    assert _s(r, BERG, "2026-11-07") == [("10:00", "19:00", "no_stick"), ("19:00", "22:00", "with_stick")]


def test_outdoor_rink_opening_on_a_date():
    body = BERG_OPEN.replace("är öppen.", "öppnar 14 november.")
    r = a.parse(_page(WEEKLY, ("Bergshamra IP", body)), TODAY)
    assert _s(r, BERG, "2026-11-13") == []
    assert _s(r, BERG, "2026-11-14")[0] == ("10:00", "19:00", "no_stick")


def test_open_outdoor_rink_without_hours_warns():
    r = a.parse(_page(WEEKLY, ('"Frimärket" vid Huvudstafältet',
                               "<p>Frimärket är öppet när det är tillräckligt kallt ute.</p>")), TODAY)
    assert r.halls[FRIM].sessions == []
    assert len(r.warnings) == 1 and r.warnings[0].startswith(FRIM + ": no opening hours")


def test_closed_outdoor_rink_has_no_sessions():
    r = a.parse(_page(WEEKLY, ("Bergshamra IP", "<p>Isbanan är stängd för säsongen.</p>")), TODAY)
    assert r.warnings == [] and r.halls[BERG].sessions == []
    # A note that the ice can be closed in mild weather does not close an open rink.
    r = a.parse(_page(WEEKLY, ("Bergshamra IP", BERG_OPEN + "<p>Isbanan kan vara stängd vid mildväder.</p>")), TODAY)
    assert _s(r, BERG, "2026-11-07")


def test_unknown_outdoor_section_warns():
    r = a.parse(_page(WEEKLY, ("Isbanan i Råsunda", "<p>Öppet 10-20</p>")), TODAY)
    assert r.warnings == ["Section 'Isbanan i Råsunda' is not handled by the adapter"]


def test_layout_change_fails_loudly():
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><p>Ny design</p></body></html>"}, TODAY)
    with pytest.raises(ParseError):
        a.parse(_page(("Ulriksdals IP", "<p>Information kommer</p>")), TODAY)
