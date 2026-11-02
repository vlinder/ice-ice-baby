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


def test_closed_for_season(raw):
    r = a.parse(raw, TODAY)
    assert list(r.halls) == [HID]
    assert r.halls[HID].sessions == []
    assert r.empty_reason and "season" in r.empty_reason.lower()
    assert (r.covers_from, r.covers_to) == ("2026-11-02", "2026-11-29")
    assert r.source_updated == "2026-06-18T00:00"
    assert r.warnings == []


def test_marker_gone_is_layout_change(raw):
    page = raw["page"].replace("Tack för den här säsongen!", "Välkommen till en ny säsong!")
    with pytest.raises(ParseError):
        a.parse({"page": page}, TODAY)


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
