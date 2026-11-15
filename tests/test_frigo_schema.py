"""FRI-GO public schedules (adapters/frigo_schema.py), against Järfälla's schedule
for two weeks saved on 2026-11-15 (one page came back in English, one in Swedish)."""
from datetime import date

import pytest

from adapters import frigo_schema as fs
from adapters import jarfalla_frigo as j
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 15)


def raw():
    return {str(r.obj): (FIXTURES / f"jarfalla_frigo_{r.obj}.html").read_text(encoding="utf-8") for r in j.RINKS}


def sessions(r, hid):
    return [(s.date, s.start, s.end, s.stick, s.note) for s in r.halls[hid].sessions]


def test_jarfalla_public_sessions_and_sticks():
    r = j.parse(raw(), TODAY)
    assert r.warnings == []
    assert (r.covers_from, r.covers_to) == ("2026-11-15", "2026-11-28")
    kh = sessions(r, "jarfalla-kallhalls-ishall")
    ji = sessions(r, "jarfalla-jarfalla-ishall")
    assert len(kh) == len(ji) == 14
    # Same as the municipality's web page for week 47.
    assert ("2026-11-16", "14:00", "16:00", "no_stick", None) in kh
    assert ("2026-11-22", "11:00", "13:00", "with_stick", None) in kh
    assert ("2026-11-16", "14:00", "16:00", "with_stick", None) in ji
    assert ("2026-11-21", "12:00", "14:00", "with_stick", None) in ji
    # School, club and maintenance bookings are not public skating.
    assert all(s[1] in ("11:00", "12:00", "14:00") for s in kh + ji)


def test_sessions_link_to_their_booking():
    r = j.parse(raw(), TODAY)
    s = r.halls["jarfalla-kallhalls-ishall"].sessions[0]
    assert s.source_url.startswith("https://jarfalla.fri-go.se/bokning/bokningvisaajax.aspx?boknr=")
    assert "objekt=42" in s.source_url and f"aktdat={s.date.replace('-', '')}" in s.source_url


def page(cells_by_day, period=("2026-11-16", "2026-11-17"), lang="Schedule"):
    """A minimal schema.aspx page: one hour row, a cell list per day."""
    days = [date.fromisoformat(period[0])]
    while days[-1] < date.fromisoformat(period[1]):
        days.append(date.fromordinal(days[-1].toordinal() + 1))
    head = "".join(f"<th>Mån&nbsp;{d.day}/{d.month}</th>" for d in days)
    tds = "".join(f'<td><div class="wrapper">{"".join(cells_by_day.get(d.isoformat(), []))}</div></td>' for d in days)
    return (f"<span>{lang} {period[0]} - {period[1]}</span>"
            f'<table class="schemagrafiskt"><tr><th>v.47</th>{head}</tr><tr><td>14</td>{tds}</tr></table>')


def booking(day, text, time, obj=42, nr=1, tip=True):
    tip_attr = (f" onmouseover=\"Tip('&lt;div class=\\'wz_tooltip\\'>Tid: x&lt;br />Aktivitet: {text}&lt;br />"
                f"Klicka för mer information&lt;/div>', BORDERWIDTH, 0)\"") if tip else ""
    return (f'<div id="cphHuvud_div_{obj}_{day.replace("-", "")}_{nr}" class="bokning"{tip_attr}>'
            f"<span>{text}<br />{time}</span></div>")


RINK = [fs.Rink(42, "x-rink", "Rink")]


def run(html, today=date(2026, 11, 16)):
    return fs.parse_schedules({"42": html}, today, rinks=RINK, base="https://x.fri-go.se/bokning/")


def test_public_test_and_stick_from_label():
    html = page({"2026-11-16": [booking("2026-11-16", "Allmänhet Utan klubbor", "14:00-15:20"),
                                booking("2026-11-16", "Förskola allmäntid", "09:00-10:30", nr=2),
                                booking("2026-11-16", "Allmänheten Disco", "18:00-20:00", nr=3)]})
    s = run(html).halls["x-rink"].sessions
    assert [(x.start, x.stick, x.note, x.tags) for x in s] == [
        ("14:00", "no_stick", None, []), ("18:00", "unknown", "Allmänheten Disco", ["disco"])]


def test_span_text_is_used_without_a_tooltip():
    html = page({"2026-11-17": [booking("2026-11-17", "Allmänheten<br />Med klubba", "14:00-16:00", tip=False)]})
    s = run(html).halls["x-rink"].sessions
    assert [(x.date, x.stick) for x in s] == [("2026-11-17", "with_stick")]


def test_days_blocked_from_viewing_are_not_covered():
    html = page({"2026-11-16": [booking("2026-11-16", "Allmänheten", "14:00-16:00")],
                 "2026-11-18": ['<div class="sparrat"><span>Blocked for<br />viewing</span></div>']},
                period=("2026-11-16", "2026-11-19"))
    r = run(html)
    assert (r.halls["x-rink"].covers_from, r.halls["x-rink"].covers_to) == ("2026-11-16", "2026-11-17")


def test_unreadable_time_is_a_warning():
    html = page({"2026-11-16": [booking("2026-11-16", "Allmänheten", "14-16")]})
    r = run(html)
    assert r.halls["x-rink"].sessions == [] and len(r.warnings) == 1 and r.warnings[0].startswith("x-rink:")


@pytest.mark.parametrize("html", [
    "<html><body>Behörighet saknas</body></html>",
    page({"2026-11-16": [booking("2026-11-17", "Allmänheten", "14:00-16:00")]}),            # wrong column
    page({"2026-11-16": [booking("2026-11-16", "Allmänheten", "14:00-16:00", obj=43)]}),    # other facility
    page({"2026-11-16": ['<div class="ledigt"></div>']}).replace("Mån&nbsp;17/11", "Tis"),  # unreadable day
])
def test_layout_changes_fail_loudly(html):
    with pytest.raises(ParseError):
        run(html)
