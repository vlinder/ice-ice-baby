"""Huddinge: public skating from Actor Smartbook's calendar (saved 2026-11-15, two
weeks), stick rule from the web page fixture (saved 2026-11-02)."""
import json
from datetime import date

import pytest

from adapters import actor_scheduler as actor
from adapters import huddinge_actor as h
from adapters.common import ParseError
from conftest import FIXTURES
from pipeline.problems import apply_corrections, load_corrections

TODAY = date(2026, 11, 15)


def raw(appointments=None):
    return {"appointments": appointments or (FIXTURES / "huddinge_actor_appointments.json").read_text(encoding="utf-8"),
            "web:page": (FIXTURES / "huddinge_web.html").read_text(encoding="utf-8")}


def sessions(r, hid):
    return [(s.date, s.start, s.end, s.stick) for s in r.halls[hid].sessions]


def test_public_sessions_from_the_calendar():
    r = h.parse(raw(), TODAY)
    assert r.warnings == []
    assert (r.covers_from, r.covers_to) == ("2026-11-15", "2026-11-28")
    assert {k: len(v.sessions) for k, v in r.halls.items()} == {
        "huddinge-bjorkangshallen-b": 14, "huddinge-segeltorpshallen": 12, "huddinge-stortorpshallen": 14,
        "huddinge-visattrahallen": 12, "huddinge-visattra-bandyplan": 0}
    # Stortorp's Wednesday is two bookings, as on the web page.
    assert [s[1:3] for s in sessions(r, "huddinge-stortorpshallen") if s[0] == "2026-11-18"] == [
        ("13:00", "14:00"), ("14:00", "14:50")]


def test_stick_comes_from_the_web_page_where_it_lists_the_same_session():
    r = h.parse(raw(), TODAY)
    b = sessions(r, "huddinge-bjorkangshallen-b")
    assert ("2026-11-16", "12:15", "14:45", "with_stick") in b
    assert ("2026-11-19", "10:15", "12:45", "no_stick") in b
    assert all(s[3] != "unknown" for s in b)


def test_shipped_correction_still_applies_to_the_web_page():
    corrected, used, unused = apply_corrections(h.NAME, raw(), load_corrections())
    assert used and not unused
    r = h.parse(corrected, TODAY)
    assert ("2026-11-16", "11:00", "12:50", "no_stick") in sessions(r, "huddinge-visattrahallen")


def appointments(*items, lo="2026-11-16", hi="2026-11-17"):
    return json.dumps({"from": lo, "to": hi, "response": {"d": list(items)}})


def appt(subject, desc, start, end):
    ms = lambda s: int(__import__("datetime").datetime.fromisoformat(s + "+00:00").timestamp() * 1000)
    return {"Subject": subject, "Description": desc, "Start": f"/Date({ms(start)})/", "End": f"/Date({ms(end)})/"}


def test_only_bookings_by_allmanheten_count_and_times_are_wall_clock():
    doc = appointments(appt("Björkängshallen B", "Allmänheten\r\n", "2026-11-16T09:00", "2026-11-16T10:30"),
                       appt("Björkängshallen B", "Grantorp Allmänna IK\r\n", "2026-11-16T11:00", "2026-11-16T12:00"),
                       appt("Björkängshallen B", "Skötsel\r\n", "2026-11-16T10:30", "2026-11-16T11:00"),
                       appt("Björkängshallen B", "Allmänheten\r\n", "2026-11-17T22:00", "2026-11-18T00:00"))
    _, _, bookings = actor.parse_appointments(doc, h.RESOURCES)
    assert [(b.date, b.start, b.end) for b in bookings] == [
        ("2026-11-16", "09:00", "10:30"), ("2026-11-17", "22:00", "24:00")]


@pytest.mark.parametrize("doc", [
    "<html>Någonting gick sönder</html>",
    json.dumps({"from": "2026-11-16", "to": "2026-11-17", "response": {"Message": "error"}}),
    appointments(appt("Björkängshallen A", "Allmänheten", "2026-11-16T09:00", "2026-11-16T10:00")),
    appointments(appt("Björkängshallen B", "Allmänheten", "2026-11-16T22:00", "2026-11-18T01:00")),
])
def test_unexpected_answers_fail_loudly(doc):
    with pytest.raises(ParseError):
        actor.parse_appointments(doc, h.RESOURCES)
