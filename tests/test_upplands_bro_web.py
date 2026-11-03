from datetime import date

import pytest

from adapters import upplands_bro_web as a
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 2)
HID = "upplands-bro-kungsangens-ip"


@pytest.fixture(scope="module")
def raw():
    return {"page": (FIXTURES / "upplands_bro_web.html").read_text(encoding="utf-8")}


@pytest.fixture(scope="module")
def pre():
    """Nov 2026: "Planerad öppning av is-banan är lucia". The left-over table still says
    "vecka 9" (last season's sportlov) but its dates were edited to "Måndag 22/2 – Söndag 28/2",
    whose weekday names only fit 2027."""
    return {"page": (FIXTURES / "upplands_bro_web_preseason.html").read_text(encoding="utf-8")}


def _page(notice: str, caption: str, rows: str) -> dict[str, str]:
    return {"page": f"""<html><body>
      <h3>Skridskor på isbanan på Kungsängens IP</h3>
      <table><caption>{caption}</caption><thead><tr><th>Dag</th><th>Tid</th></tr></thead>
      <tbody>{rows}</tbody></table>
      <p>{notice}</p>
      <h3>Skridskor på Mälaren och andra sjöar</h3>
    </body></html>"""}


def test_closed_for_season(raw):
    r = a.parse(raw, TODAY)
    assert list(r.halls) == [HID]
    assert r.halls[HID].sessions == []
    assert r.empty_reason and "season" in r.empty_reason.lower()
    assert (r.covers_from, r.covers_to) == ("2026-11-02", "2026-11-29")
    assert r.source_updated == "2026-06-18T00:00"
    assert r.warnings == []


def test_marker_gone_and_no_current_times_is_layout_change(raw):
    page = raw["page"].replace("Tack för den här säsongen!", "Välkommen till en ny säsong!")
    with pytest.raises(ParseError):
        a.parse({"page": page}, TODAY)


def test_preseason_until_planned_opening(pre):
    r = a.parse(pre, date(2026, 11, 3))
    assert r.halls[HID].sessions == []
    assert r.empty_reason and "lucia" in r.empty_reason
    assert (r.covers_from, r.covers_to) == ("2026-11-03", "2026-11-30")
    assert r.source_updated == "2026-11-03T00:00"


def test_preseason_window_stops_before_lucia(pre):
    r = a.parse(pre, date(2026, 11, 30))
    assert (r.covers_from, r.covers_to) == ("2026-11-30", "2026-12-12")


def test_preseason_after_planned_opening_is_layout_change(pre):
    with pytest.raises(ParseError):
        a.parse(pre, date(2026, 12, 13))


def test_old_table_is_dated_by_its_caption_week(pre):
    # 22/2–28/2 under "vecka 9" is last season (2026), not week 8 of 2027: ignored, no warning.
    r = a.parse(pre, date(2026, 11, 3))
    assert r.warnings == []
    assert a.parse(pre, date(2027, 2, 20)).warnings == []


def test_current_table_that_contradicts_itself_is_skipped():
    rows = ("<tr><td>Måndag 22/2</td><td>10.00 - 21.00</td></tr>"
            "<tr><td>Tisdag 23/2</td><td>10.00 - 21.00</td></tr>")
    r = a.parse(_page("Planerad öppning av is-banan är lucia.", "Öppettider för isbanan vecka 9", rows),
                date(2026, 2, 1))
    assert r.halls[HID].sessions == [] and r.empty_reason
    assert len(r.warnings) == 1
    assert r.warnings[0].startswith(f"{HID}: ") and "'Måndag 22/2'" in r.warnings[0]
    assert "week 9 of 2026" in r.warnings[0]


def test_caption_week_never_matching_is_skipped():
    rows = "<tr><td>Måndag 4/1</td><td>10.00 - 21.00</td></tr>"
    r = a.parse(_page("Planerad öppning av is-banan är lucia.", "Öppettider vecka 30", rows),
                date(2026, 12, 1))
    assert r.halls[HID].sessions == [] and r.empty_reason
    assert len(r.warnings) == 1 and "never week 30" in r.warnings[0]


def test_preseason_reads_published_times():
    rows = ("<tr><td>Lördag 12/12</td><td>10.00 - 16.00</td></tr>"
            "<tr><td>Söndag 13/12</td><td>10.00-16.00, 18.00-21.00</td></tr>")
    r = a.parse(_page("Planerad öppning av is-banan är lucia.", "Öppettider vecka 50", rows),
                date(2026, 12, 1))
    assert r.empty_reason is None and r.warnings == []
    assert [(s.date, s.start, s.end) for s in r.halls[HID].sessions] == [
        ("2026-12-12", "10:00", "16:00"), ("2026-12-13", "10:00", "16:00"), ("2026-12-13", "18:00", "21:00")]
    assert (r.covers_from, r.covers_to) == ("2026-12-12", "2026-12-13")


def test_in_season_dated_rows():
    rows = ("<tr><td>Måndag 4/1</td><td>10.00 - 21.00</td></tr>"
            "<tr><td>Tisdag 5/1</td><td>Stängt</td></tr>"
            "<tr><td>Onsdag 6/1</td><td>10-30-21.00</td></tr>"
            "<tr><td>Torsdag 7/1</td><td>Se nyheter</td></tr>")
    r = a.parse(_page("Välkommen!", "Öppettider för isbanan vecka 1", rows), date(2027, 1, 5))
    assert r.empty_reason is None
    assert r.halls[HID].sessions == []
    assert (r.covers_from, r.covers_to) == ("2027-01-05", "2027-01-07")
    assert len(r.warnings) == 2 and all(w.startswith(f"{HID}: ") for w in r.warnings)
    r = a.parse(_page("Välkommen!", "Öppettider för isbanan vecka 1", rows), date(2027, 1, 4))
    assert [(s.date, s.start, s.end) for s in r.halls[HID].sessions] == [("2027-01-04", "10:00", "21:00")]


def test_marker_with_current_dates_is_layout_change(raw):
    # The old vecka 9 table (Monday 23/2 = 2026) is current in February 2026.
    with pytest.raises(ParseError):
        a.parse(raw, date(2026, 2, 20))


def test_weekday_picks_the_year():
    # "Måndag 23/2" is closer to 2027-02-23 than 2026-02-23 from today, but only 2026 is a Monday.
    assert a._row_date("Måndag", 23, 2, TODAY) == date(2026, 2, 23)


def test_status_tables_elsewhere_are_ignored():
    page = """<html><body>
      <h3>Skridskor på isbanan på Kungsängens IP</h3><p><strong>Tack för den här säsongen!</strong></p>
      <h3>Status skidspår och skridskoisar</h3>
      <table><tbody><tr><td>Gröna dalen</td><td>Ja</td><td>20270123</td></tr></tbody></table>
    </body></html>"""
    r = a.parse({"page": page}, TODAY)
    assert r.halls[HID].sessions == [] and r.empty_reason


def test_missing_heading_is_layout_change():
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><p>Tack för den här säsongen!</p></body></html>"}, TODAY)
