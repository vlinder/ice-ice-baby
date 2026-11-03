"""Ekerö kommun: Interbook booking list "Allmänhetens åkning".

Ekerö moved from FRI-GO to Interbook on 2 Nov 2026. ekero.se ("Gymnastiksalar,
idrottsplatser & ishallar") links "Hitta alla tider för allmänhetens åkning här"
to a DisplayBookingList whose `c` parameter is base64 JSON: title "Allmänhetens
åkning", 60 days ("d"), 50 rows per page ("m"), objects 203 and 241
(Vikingahallen at Träkvistavallen, Allhallen at Svanängen IP).

Markup (Nov 2026): every page of the list is in the same HTML, one table each,

    <div data-booking-list-page="1">
      <table><caption>Bokningar 2026-11-03 - 2027-01-01</caption>
        <thead>Datum | Tid | Anläggning | Objekt | Kund | Aktivitet</thead>
        <tbody><tr><th scope="rowgroup">Tisdag 2026-11-03</th></tr>
               <tr><td>2026-11-03</td><td>14:00-15:50</td><td>Träkvistavallen</td>
                   <td>Vikingahallen</td><td>Allmänhetens åkning</td>
                   <td>Alltid hjälm utan klubba</td></tr> ...

`covers` is the caption's range (the list starts tomorrow). If the last page is
full, the list may continue beyond what is shown, so the last listed day is left
out. The stick rule is in Aktivitet ("Alltid hjälm med/utan klubba"); other
wording, e.g. "Disco", gives stick "unknown" with the wording as note.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from bs4 import BeautifulSoup

from .common import (
    STICK_UNKNOWN, AdapterResult, HallResult, ParseError, Session,
    hhmm, http_get, norm_space, slugify, stick_from_text,
)

NAME = "ekero-interbook"
MUNICIPALITY = "Ekerö"
SOURCE_URL = ("https://ekero.interbook.se/DisplayBookingList?c=eyJ2IjoxLCJ0IjoiQWxsbcOkbmhldGVucyDDpWtuaW5nIiwic3Qi"
              "OnRydWUsImQiOjYwLCJtIjo1MCwicCI6ZmFsc2UsInMiOjAsImZtdCI6MCwicmYiOjIsImYiOlsxLDIsNCw1LDYsMTBdLCJvIjpb"
              "MjAzLDI0MV0sImJ0IjpbXSwiZXUiOltdLCJhdCI6WzIyMiwyMjMsMzZdfQ2")
INFO_URL = "https://www.ekero.se/uppleva-gora/hitta-och-boka-lokaler/gymnastiksalar-idrottsplatser--ishallar"
PAGE_ROWS = 50

# (Anläggning, Objekt) -> hall. The ids are the ones the FRI-GO adapter used, so history continues.
HALLS = {
    ("träkvistavallen", "vikingahallen"): ("ekero-trakvistavallen", "Träkvistavallen", "Vikingahallen"),
    ("svanängen ip", "allhallen"): ("ekero-svanangen-ip", "Svanängen IP", "Allhallen"),
}
HEADER = ["datum", "tid", "anläggning", "objekt", "kund", "aktivitet"]
CAPTION_RE = re.compile(r"bokningar\s+(\d{4}-\d{2}-\d{2})\s*-\s*(\d{4}-\d{2}-\d{2})", re.I)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")


def fetch() -> dict[str, str]:
    return {"page": http_get(SOURCE_URL)}


def _hall(place: str, obj: str) -> tuple[str, str, str | None]:
    known = HALLS.get((place.lower(), obj.lower()))
    if known:
        return known
    return f"ekero-{slugify(place + ' ' + obj)}", place, obj or None


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    soup = BeautifulSoup(raw["page"], "lxml")
    tables = soup.select("[data-booking-list] table")
    if not tables:
        raise ParseError("No booking list tables – has the Interbook page changed?")

    ranges: set[tuple[str, str]] = set()
    halls = {hid: HallResult(hall_id=hid, name=n, rink=r) for hid, n, r in HALLS.values()}
    warnings: list[str] = []
    last_page_rows = 0
    for table in tables:
        cap = table.find("caption")
        m = CAPTION_RE.search(norm_space(cap.get_text(" "))) if cap else None
        if not m:
            raise ParseError(f"Unexpected list caption {cap.get_text(' ', strip=True) if cap else None!r}")
        ranges.add((m[1], m[2]))
        head = [norm_space(th.get_text(" ")).lower() for th in table.select("thead th")]
        if head != HEADER:
            raise ParseError(f"Unexpected table header {head}")
        rows = [tr for tr in table.select("tbody tr") if tr.find("td")]
        last_page_rows = len(rows)
        for tr in rows:
            cells = [norm_space(td.get_text(" ")) for td in tr.find_all("td")]
            if len(cells) != len(HEADER):
                warnings.append(f"ekero: could not read row {' | '.join(cells)!r}")
                continue
            day, time, place, obj, customer, activity = cells
            hid, name, rink = _hall(place, obj)
            tm = TIME_RE.match(time)
            if not DATE_RE.match(day) or not tm:
                warnings.append(f"{hid}: could not read row {' | '.join(cells)!r}")
                continue
            if "allmänhet" not in customer.lower():
                warnings.append(f"{hid}: row not booked as public skating {' | '.join(cells)!r}")
                continue
            s, e = hhmm(tm[1], tm[2]), hhmm(tm[3], tm[4])
            if e <= s:
                raise ParseError(f"end before start in row {cells}")
            stick = stick_from_text(activity)
            hall = halls.setdefault(hid, HallResult(hall_id=hid, name=name, rink=rink))
            stick = stick or STICK_UNKNOWN
            if any((x.date, x.start, x.end, x.stick) == (day, s, e, stick) for x in hall.sessions):
                continue  # pages overlap at their boundaries
            hall.sessions.append(Session(date=day, start=s, end=e, stick=stick,
                                         source_url=SOURCE_URL, note=None if stick != STICK_UNKNOWN else (activity or None)))

    if len(ranges) != 1:
        raise ParseError(f"List pages disagree on the period: {sorted(ranges)}")
    covers_from, covers_to = ranges.pop()
    if last_page_rows >= PAGE_ROWS:
        # A full last page: there may be more rows than the page shows; don't claim its last day.
        last = max((s.date for h in halls.values() for s in h.sessions), default=covers_to)
        covers_to = min(covers_to, (date.fromisoformat(last) - timedelta(days=1)).isoformat())
        for h in halls.values():
            h.sessions = [s for s in h.sessions if s.date <= covers_to]
    for h in halls.values():
        h.sessions.sort(key=lambda s: (s.date, s.start, s.end))
    return AdapterResult(covers_from=covers_from, covers_to=covers_to, source_updated=None,
                         halls=halls, warnings=warnings)
