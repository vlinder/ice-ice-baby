from datetime import date

import pytest

from adapters import ekero_interbook as a
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 2)


def page():
    return (FIXTURES / "ekero_interbook.html").read_text(encoding="utf-8")


def sessions(r, hid):
    return [(s.date, s.start, s.end, s.stick, s.note) for s in r.halls[hid].sessions]


def test_all_pages_are_read():
    r = a.parse({"page": page()}, TODAY)
    assert r.warnings == []
    assert (r.covers_from, r.covers_to) == ("2026-11-03", "2027-01-01")
    assert {k: len(v.sessions) for k, v in r.halls.items()} == {"ekero-trakvistavallen": 74, "ekero-svanangen-ip": 87}


def test_sessions_and_sticks():
    r = a.parse({"page": page()}, TODAY)
    t = sessions(r, "ekero-trakvistavallen")
    assert t[0] == ("2026-11-03", "14:00", "15:50", "no_stick", None)
    assert ("2026-11-04", "14:00", "16:20", "with_stick", None) in t
    assert ("2026-11-21", "13:00", "16:00", "with_stick", None) in t  # first row of page 2
    assert t[-1][0] <= "2027-01-01"
    disco = [s for s in sessions(r, "ekero-svanangen-ip") if s[0] == "2026-11-21" and s[1] == "16:30"]
    assert disco == [("2026-11-21", "16:30", "19:00", "unknown", "Disco")]
    tagged = {(x.date, x.start) for h in r.halls.values() for x in h.sessions if x.tags == ["disco"]}
    assert ("2026-11-21", "16:30") in tagged
    assert all(x.note == "Disco" for h in r.halls.values() for x in h.sessions if x.tags)


def _table(rows, caption="Bokningar 2026-11-03 - 2026-11-10"):
    head = "".join(f"<th>{h}</th>" for h in ["Datum", "Tid", "Anläggning", "Objekt", "Kund", "Aktivitet"])
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return (f'<div data-booking-list><div data-booking-list-page="1"><table><caption>{caption}</caption>'
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></div>")


def row(day="2026-11-04", time="14:00-15:00", customer="Allmänhetens åkning", activity="Alltid hjälm med klubba"):
    return (day, time, "Träkvistavallen", "Vikingahallen", customer, activity)


def test_a_full_last_page_does_not_claim_its_last_day():
    rows = [row(day=f"2026-11-{4 + i // 10:02d}", time=f"{8 + i % 10}:00-{8 + i % 10}:45") for i in range(50)]
    r = a.parse({"page": _table(rows, "Bokningar 2026-11-03 - 2026-12-31")}, TODAY)
    assert r.covers_to == "2026-11-07"
    assert max(s.date for s in r.halls["ekero-trakvistavallen"].sessions) == "2026-11-07"


def test_rows_booked_by_another_customer_are_skipped():
    # e.g. "2026-12-20 | 11:00-14:00 | Svanängen IP | Allhallen | Mälarö Hockeyförening | Disco":
    # a club event, not public skating, even though the list includes it.
    rows = [row(customer="Ekerö IK"), row(time="15:00-16:00"),
            ("2026-11-08", "11:00-14:00", "Svanängen IP", "Allhallen", "Mälarö Hockeyförening", "Disco")]
    r = a.parse({"page": _table(rows)}, TODAY)
    assert sessions(r, "ekero-trakvistavallen") == [("2026-11-04", "15:00", "16:00", "with_stick", None)]
    assert r.halls["ekero-svanangen-ip"].sessions == []
    assert r.warnings == []


def test_layout_change_fails_loudly():
    with pytest.raises(ParseError):
        a.parse({"page": "<main><p>Inga bokningar</p></main>"}, TODAY)
    with pytest.raises(ParseError):
        a.parse({"page": _table([row()], caption="Något annat")}, TODAY)
