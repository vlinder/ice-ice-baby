"""Huddinge kommun: public skating from the booking calendar in Huddinge's Actor
Smartbook (huddinge.actorsmartbook.se), with the stick rule from the
municipality's web page.

The calendar shows who booked: public skating is booked by "Allmänheten". It
holds the whole season and reflects the closures the web page lists under
"Avvikande öppettider" (10 Nov 2026: no public bookings). It has no stick rule,
so the web page (read by huddinge_web.py) supplies it for sessions it lists
with exactly the same date and times; other sessions get stick "unknown".
If the web page can't be read, the source fails like any other layout change.
See actor_scheduler.py for the calendar format."""
from __future__ import annotations

from datetime import date

from . import actor_scheduler as actor
from . import huddinge_web as web
from .common import STICK_UNKNOWN, AdapterResult, HallResult, Session, today_local

NAME = "huddinge-actor"
MUNICIPALITY = "Huddinge"
HOST = "huddinge.actorsmartbook.se"
SOURCE_URL = f"https://{HOST}/ResourceBookingRequest.aspx"

RESOURCES = [
    actor.Resource(234, "Björkängshallen B", web.hall_id("Björkängshallen", "B"), "Björkängshallen", "B"),
    actor.Resource(329, "Segeltorps IP Segeltorpshallen", web.hall_id("Segeltorpshallen", None), "Segeltorpshallen"),
    actor.Resource(348, "Stortorps IP Stortorpshallen", web.hall_id("Stortorpshallen", None), "Stortorpshallen"),
    actor.Resource(391, "Visättra Sportcenter Visättrahallen", web.hall_id("Visättrahallen", None), "Visättrahallen"),
    actor.Resource(380, "Visättra Sportcenter Bandyplan", web.hall_id("Visättra bandyplan", None), "Visättra bandyplan"),
]
WEB_PREFIX = "web:"


def fetch() -> dict[str, str]:
    raw = {"appointments": actor.fetch_appointments(HOST, RESOURCES, today_local())}
    raw.update({WEB_PREFIX + k: v for k, v in web.fetch().items()})
    return raw


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    lo, hi, bookings = actor.parse_appointments(raw["appointments"], RESOURCES)
    page = web.parse({k[len(WEB_PREFIX):]: v for k, v in raw.items() if k.startswith(WEB_PREFIX)}, today)
    sticks = {(hid, s.date, s.start, s.end): (s.stick, s.note)
              for hid, h in page.halls.items() for s in h.sessions}
    halls = {r.hall_id: HallResult(hall_id=r.hall_id, name=r.name, rink=r.rink) for r in RESOURCES}
    start = max(today, lo).isoformat()
    for b in bookings:
        if b.date < start:
            continue
        stick, note = sticks.get((b.resource.hall_id, b.date, b.start, b.end), (STICK_UNKNOWN, None))
        halls[b.resource.hall_id].sessions.append(Session(
            date=b.date, start=b.start, end=b.end, stick=stick, source_url=SOURCE_URL, note=note))
    return AdapterResult(covers_from=start, covers_to=hi.isoformat(), source_updated=page.source_updated,
                         halls=halls)
