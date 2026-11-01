from datetime import date

import pytest

from adapters import stockholm_motionera as a
from adapters.common import ParseError
from conftest import FIXTURES


@pytest.fixture(scope="module")
def result():
    raw = {"page": (FIXTURES / "stockholm_motionera.html").read_text(encoding="utf-8")}
    return a.parse(raw, date(2026, 11, 1))


def test_covers_both_weeks(result):
    assert (result.covers_from, result.covers_to) == ("2026-10-26", "2026-11-08")
    assert result.source_updated == "2026-10-27T16:03"
    assert result.warnings == []


def test_known_halls_present(result):
    assert len(result.halls) == 11
    assert "stockholm-ostermalms-idrottsplats-ishallen" in result.halls
    assert "stockholm-stora-mossens-idrottsplats-b-hallen" in result.halls


def test_specific_sessions(result):
    s = [x for x in result.halls["stockholm-ostermalms-idrottsplats-ishallen"].sessions if x.date == "2026-10-30"]
    assert [(x.start, x.end, x.stick) for x in s] == [("08:00", "14:00", "shared")]
    b = [x for x in result.halls["stockholm-stora-mossens-idrottsplats-b-hallen"].sessions if x.date == "2026-10-30"]
    assert b[0].stick == "with_stick"


def test_no_times_line_means_no_session(result):
    gubb = result.halls["stockholm-gubbangens-idrottsplats-bandyhallen"].sessions
    assert not [x for x in gubb if x.date == "2026-10-31"]


@pytest.mark.parametrize("title,today,expected", [
    ("26 oktober–1 november", date(2026, 11, 1), (date(2026, 10, 26), date(2026, 11, 1))),
    ("2–8 november", date(2026, 11, 1), (date(2026, 11, 2), date(2026, 11, 8))),
    ("28 december–3 januari", date(2026, 12, 20), (date(2026, 12, 28), date(2027, 1, 3))),
    ("28 december–3 januari", date(2027, 1, 2), (date(2026, 12, 28), date(2027, 1, 3))),
])
def test_week_titles(title, today, expected):
    assert a._parse_week_title(title, today) == expected


def test_week_title_must_be_monday_to_sunday():
    with pytest.raises(ParseError):
        a._parse_week_title("27 oktober–2 november", date(2026, 11, 1))


def _page(lines: str) -> dict:
    return {"page": f"""
    <button class="accordion-item__header" aria-controls="w1"><span class="accordion-item__title">2–8 november</span></button>
    <div id="w1"><ul><li>Delad is betyder...</li></ul>
      <h3>Testhallen</h3><h4>Ishallen</h4><ul>{lines}</ul></div>"""}


def test_multiple_times_on_one_day_and_changed_marker():
    r = a.parse(_page("<li><strong>Lördag: 08.00–09.30, delad is och 12.00–13.00, utan klubba</strong></li>"), date(2026, 11, 1))
    s = r.halls["stockholm-testhallen-ishallen"].sessions
    assert [(x.date, x.start, x.end, x.stick, x.changed) for x in s] == [
        ("2026-11-07", "08:00", "09:30", "shared", True),
        ("2026-11-07", "12:00", "13:00", "no_stick", True),
    ]


def test_unreadable_line_becomes_warning():
    r = a.parse(_page("<li>Måndag: se anslag</li>"), date(2026, 11, 1))
    assert r.warnings and "se anslag" in r.warnings[0]


def test_layout_change_fails_loudly():
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><p>Ny design!</p></body></html>"}, date(2026, 11, 1))
