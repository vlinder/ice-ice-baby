import json
from datetime import date

import pytest

from adapters import sundbyberg_web as a
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 2)
HID = "sundbyberg-tulehallen"


@pytest.fixture(scope="module")
def result():
    raw = {"page": (FIXTURES / "sundbyberg_web.html").read_text(encoding="utf-8")}
    return a.parse(raw, TODAY)


def _s(result, day):
    return [(x.start, x.end, x.stick) for x in result.halls[HID].sessions if x.date == day]


def test_hall_and_meta(result):
    assert set(result.halls) == {HID}
    assert result.halls[HID].name == "Tulehallen"
    assert result.source_updated == "2026-10-28T00:00"
    assert result.warnings == []  # includes the cross-check against the 7 rendered days


def test_covers_widget_range(result):
    # Today to the Sunday of the last week the widget can page to (week 50).
    assert (result.covers_from, result.covers_to) == ("2026-11-02", "2026-12-13")


def test_sessions(result):
    assert _s(result, "2026-11-02") == [("08:00", "15:15", "shared")]
    # Deviation overrides the template (template Saturday is 12:00–14:00).
    assert _s(result, "2026-11-07") == [("12:00", "15:15", "shared")]
    assert _s(result, "2026-11-08") == [("08:00", "09:15", "shared"), ("12:00", "15:15", "shared")]
    assert _s(result, "2026-11-14") == [("12:00", "16:15", "shared")]
    # A Saturday without deviation uses the template.
    assert _s(result, "2026-12-05") == [("12:00", "14:00", "shared")]
    assert len(result.halls[HID].sessions) == 48


def _page(state: dict, prose: str = "Ishallen är öppen för allmänheten från och med vardagar från vecka 35 "
          "till och med vecka 17 samt helger från vecka 41 till och med 17.") -> dict:
    return {"page": f"""<html><body><main>
    <h2>Allmänhetens åkning i Tulehallen</h2><p>Under allmänhetens åkning är planen uppdelad.</p>
    <p>{prose}</p>
    <div class="sv-se-soleil-openingHours-week" id="x"><ul></ul>
    <script>AppRegistry.registerInitialState('12.x',{json.dumps(state)});</script></div>
    </main></body></html>"""}


def _state(**kw):
    st = {"openHours": [[{"open": "08:00", "close": "15:15", "text": ""}]] * 5
          + [[{"open": "12:00", "close": "14:00", "text": ""}]] * 2,
          "deviantHours": [], "currentWeek": {"week": 45, "year": 2026}, "nextWeeks": [{"week": 46, "year": 2026}]}
    st.update(kw)
    return st


def test_closed_deviation_and_season():
    r = a.parse(_page(_state(deviantHours=[{"date": "2026-11-03", "closed": True}])), TODAY)
    assert (r.covers_from, r.covers_to) == ("2026-11-02", "2026-11-15")
    assert [x for x in r.halls[HID].sessions if x.date == "2026-11-03"] == []
    assert len(r.halls[HID].sessions) == 13


def test_weekends_outside_season_are_skipped():
    st = _state(currentWeek={"week": 38, "year": 2026}, nextWeeks=[{"week": 39, "year": 2026}])
    r = a.parse(_page(st), date(2026, 9, 14))
    days = {x.date for x in r.halls[HID].sessions}
    assert "2026-09-14" in days and "2026-09-19" not in days


def test_bad_time_warns():
    st = _state(deviantHours=[{"date": "2026-11-04", "open": "8.00", "close": "15:15", "closed": False}])
    r = a.parse(_page(st), TODAY)
    assert r.warnings and "2026-11-04" in r.warnings[0]


def test_layout_change_fails_loudly():
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><h2>Allmänhetens åkning i Tulehallen</h2></body></html>"}, TODAY)
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><h2>Ishallen</h2></body></html>"}, TODAY)
    with pytest.raises(ParseError):
        a.parse(_page({"foo": 1}), TODAY)
