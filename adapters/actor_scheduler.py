"""Shared reader for the public booking calendar in Actor Smartbook
(`<kommun>.actorsmartbook.se/ResourceBookingRequest.aspx`, "Boka/info").

The calendar is a Telerik RadScheduler. Its page script loads the bookings with
a JSON POST to `ResourceBookingRequest.aspx/GetAppointments` (after a GET of the
page for its session cookie):

    {"schedulerInfo": {"ViewStart": "/Date(<ms>)/", "ViewEnd": "/Date(<ms>)/",
                       "ResourceFilter": "234,329", "EnableDescriptionField": true, ...}}

and gets back `{"d": [appointment, ...]}`:

    {"__type": "Telerik.Web.UI.AppointmentData", "ID": 220017,
     "Start": "/Date(1795100400000)/", "End": "/Date(1795107600000)/",
     "Subject": "Stortorps IP Stortorpshallen",          the resource's name
     "Description": "Allmänheten\\r\\n", ...}             the booker

Times are Stockholm wall-clock time sent as if they were UTC: /Date(ms)/ read
as UTC gives the local time on the calendar (checked against Huddinge's web
page, Nov 2026).

What the public sees depends on the municipality's settings: Huddinge shows
who booked (public skating is booked by "Allmänheten"); Täby, Sigtuna and
Sundbyberg show anonymous busy blocks, which this reader cannot use.

Rules:
- A booking is public skating when its booker is "Allmänheten" (whole word:
  "Grantorp Allmänna IK" is a club).
- The calendar has no stick rule; the municipality's module supplies it.
- `covers` is the period asked for. A Subject that is not one of the expected
  resources -> ParseError (the filter didn't apply); a response that is not
  an appointment list -> ParseError.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import requests

from .common import USER_AGENT, ParseError

HORIZON_DAYS = 42

DATE_MS_RE = re.compile(r"^/Date\((-?\d+)\)/$")
PUBLIC_RE = re.compile(r"^allmänheten\b", re.I)


@dataclass(frozen=True)
class Resource:
    resource_id: int     # Actor ResourceId
    subject: str         # the resource's name as the calendar's Subject shows it
    hall_id: str
    name: str
    rink: str | None = None


@dataclass(frozen=True)
class Booking:
    resource: Resource
    date: str
    start: str
    end: str


def period(today: date, days: int = HORIZON_DAYS) -> tuple[date, date]:
    return today, today + timedelta(days=days - 1)


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def fetch_appointments(host: str, resources: list[Resource], today: date, days: int = HORIZON_DAYS) -> str:
    """Return the GetAppointments JSON, prefixed with the period asked for."""
    lo, hi = period(today, days)
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    page = f"https://{host}/ResourceBookingRequest.aspx"
    s.get(page, timeout=60).raise_for_status()
    body = {"schedulerInfo": {
        "ViewStart": f"/Date({_ms(lo)})/", "ViewEnd": f"/Date({_ms(hi + timedelta(days=1))})/",
        "EnableDescriptionField": True, "EventFilter": "",
        "ResourceFilter": ",".join(str(r.resource_id) for r in resources),
        "SelectedArenaId": 0, "showOnlyMyBookings": False,
    }}
    # ASP.NET AJAX reads dates only in its escaped form, "\/Date(ms)\/".
    payload = json.dumps(body).replace('"/Date(', '"\\/Date(').replace(')/"', ')\\/"')
    r = s.post(page + "/GetAppointments", data=payload.encode(), timeout=90,
               headers={"Content-Type": "application/json; charset=utf-8"})
    r.raise_for_status()
    return json.dumps({"from": lo.isoformat(), "to": hi.isoformat(), "response": r.json()}, ensure_ascii=False)


def _local(value: str) -> datetime:
    m = DATE_MS_RE.match(value or "")
    if not m:
        raise ParseError(f"Unreadable appointment time {value!r}")
    return datetime.fromtimestamp(int(m[1]) / 1000, timezone.utc).replace(tzinfo=None)


def parse_appointments(doc: str, resources: list[Resource]) -> tuple[date, date, list[Booking]]:
    try:
        data = json.loads(doc)
        lo, hi = date.fromisoformat(data["from"]), date.fromisoformat(data["to"])
        items = data["response"]["d"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ParseError(f"Actor calendar answer is not an appointment list: {exc}") from None
    if not isinstance(items, list):
        raise ParseError("Actor calendar answer is not an appointment list")
    by_subject = {r.subject: r for r in resources}
    out: list[Booking] = []
    for a in items:
        subject = (a.get("Subject") or "").strip()
        res = by_subject.get(subject)
        if res is None:
            raise ParseError(f"Actor calendar returned a booking for {subject!r}, not one of the requested rinks")
        if not PUBLIC_RE.match((a.get("Description") or "").strip()):
            continue
        s, e = _local(a.get("Start")), _local(a.get("End"))
        if e.date() == s.date() + timedelta(days=1) and e.time().hour == 0 and e.time().minute == 0:
            end = "24:00"
        elif e.date() != s.date():
            raise ParseError(f"Booking {a.get('ID')} spans several days ({a.get('Start')}–{a.get('End')})")
        else:
            end = e.strftime("%H:%M")
        if not lo <= s.date() <= hi:
            continue
        out.append(Booking(res, s.date().isoformat(), s.strftime("%H:%M"), end))
    out.sort(key=lambda b: (b.resource.hall_id, b.date, b.start, b.end))
    return lo, hi, out
