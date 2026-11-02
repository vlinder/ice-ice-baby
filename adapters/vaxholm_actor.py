"""Vaxholm: Actor Smartbook booking-system export grid for Saneco Hallen

Vaxholms stad publishes public-skating times as an Actor Smartbook "export grid"
(a Telerik RadGrid page meant for info screens; it refreshes itself every two
minutes). The `com`/`con` query parameters select the customer and the export
configuration; the page has no form fields or script options for a date range,
and extra query parameters (days, from/to, startdate/enddate) are ignored, so
the period shown is whatever the export is configured with on Actor's side.

Layout (as of Nov 2026):

    <table class="rgMasterTable">
      <thead><tr><th class="rgHeader">Lokal</th><th>Start</th><th>Slut</th><th>Information</th></tr></thead>
      <tfoot>(hidden command row)</tfoot>
      <tbody>
        <tr class="rgNoRecords"><td colspan="4"><div>No records to display.</div></td></tr>
      </tbody>
    </table>

When there are bookings, RadGrid renders one `<tr class="rgRow">` / `<tr class="rgAltRow">`
per booking with four <td>s. Start/Slut are DateTime columns; the grid's default
format is "yyyy-MM-dd HH:mm:ss". Accepted: "YYYY-MM-DD HH:MM" with optional ":00"
seconds; anything else raises ParseError so a human looks. An end of 00:00 on the
next day is read as 24:00. Start and end on other different days -> ParseError.

Lokal containing "Saneco" -> `vaxholm-saneco-hallen`; any other Lokal gets a hall id
from slugify (the pipeline flags it as new). Stick comes from the Information
text; without wording it is unknown and the text is kept as the note. Rows whose
Information says the time is cancelled ("inställd") are skipped.

"No records to display" -> the known hall with no sessions, `empty_reason` set,
covers = recurring_window(today). With rows, covers = today .. last listed date.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from bs4 import BeautifulSoup

from .common import (
    STICK_UNKNOWN, AdapterResult, HallResult, ParseError, Session,
    http_get, norm_space, recurring_window, slugify, stick_from_text,
)

NAME = "vaxholm-actor"
MUNICIPALITY = "Vaxholm"
SOURCE_URL = ("https://exportservice.actorsmartbook.se/ExportGridStyle.aspx"
              "?com=6bb4f0a4-3cd1-49c9-84e9-4e72a5e3aa16&con=1d8d1d00-2c01-4187-b4b9-ab53de6d1981")

HALL_NAME = "Saneco Hallen"
HALL_ID = "vaxholm-saneco-hallen"

EXPECTED_HEADERS = ["Lokal", "Start", "Slut", "Information"]
DATETIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})(?::00)?$")
NO_RECORDS_RE = re.compile(r"^No records to display\.?$", re.I)
CANCELLED_RE = re.compile(r"inställ|avbokad|stängt|cancel", re.I)


def fetch() -> dict[str, str]:
    return {"page": http_get(SOURCE_URL)}


def _datetime(text: str) -> tuple[date, str]:
    m = DATETIME_RE.match(text)
    if not m:
        raise ParseError(f"Unexpected date/time format {text!r}")
    y, mo, d, hh, mm = (int(x) for x in m.groups())
    if hh > 23 or mm > 59:
        raise ParseError(f"Invalid time {text!r}")
    try:
        return date(y, mo, d), f"{hh:02d}:{mm:02d}"
    except ValueError:
        raise ParseError(f"Invalid date {text!r}") from None


def _hall_for(lokal: str) -> tuple[str, str]:
    if "saneco" in lokal.lower():
        return HALL_ID, HALL_NAME
    slug = slugify(lokal)
    if not slug:
        raise ParseError(f"Empty Lokal {lokal!r}")
    return "vaxholm-" + slug, lokal


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    soup = BeautifulSoup(raw["page"], "lxml")
    table = soup.select_one("#RadGrid1 table.rgMasterTable")
    if table is None:
        raise ParseError("No RadGrid master table – has the export layout changed?")
    headers = [norm_space(th.get_text()) for th in table.select("thead th.rgHeader")]
    if headers != EXPECTED_HEADERS:
        raise ParseError(f"Unexpected grid headers {headers!r}")
    tbody = table.find("tbody", recursive=False)
    rows = tbody.find_all("tr", recursive=False) if tbody else []
    if not rows:
        raise ParseError("Grid has no body rows")

    halls: dict[str, HallResult] = {HALL_ID: HallResult(hall_id=HALL_ID, name=HALL_NAME, rink=None)}
    warnings: list[str] = []
    no_records = False
    dates: list[date] = []
    seen: set[tuple] = set()
    for tr in rows:
        classes = tr.get("class") or []
        if "rgNoRecords" in classes:
            text = norm_space(tr.get_text(" "))
            if not NO_RECORDS_RE.match(text):
                raise ParseError(f"Unexpected empty-grid text {text!r}")
            no_records = True
            continue
        if not ({"rgRow", "rgAltRow"} & set(classes)):
            raise ParseError(f"Unexpected grid row {norm_space(tr.get_text(' '))!r} (class {classes})")
        cells = [norm_space(td.get_text(" ")) for td in tr.find_all("td", recursive=False)]
        if len(cells) != 4:
            raise ParseError(f"Expected 4 cells, got {cells!r}")
        lokal, start, slut, info = cells
        d1, s = _datetime(start)
        d2, e = _datetime(slut)
        if d2 == d1 + timedelta(days=1) and e == "00:00":
            d2, e = d1, "24:00"
        if d2 != d1:
            raise ParseError(f"Booking spans several days: {cells!r}")
        if e <= s:
            raise ParseError(f"End not after start: {cells!r}")
        if d1 < today:
            continue
        dates.append(d1)
        if info and CANCELLED_RE.search(info):
            continue  # cancelled time; the day is still covered
        if s < "05:00":
            warnings.append(f"time outside 05-24: {cells!r}")
            continue
        hid, name = _hall_for(lokal)
        hall = halls.setdefault(hid, HallResult(hall_id=hid, name=name, rink=None))
        stick = stick_from_text(info) if info else None
        note = None
        if stick is None:
            stick, note = STICK_UNKNOWN, (info or None)
        key = (hid, d1, s, e, stick)
        if key in seen:
            continue
        seen.add(key)
        hall.sessions.append(Session(date=d1.isoformat(), start=s, end=e, stick=stick,
                                     source_url=SOURCE_URL, note=note))

    if no_records and len(rows) > 1:
        raise ParseError("Grid has both 'No records' and data rows")

    for hall in halls.values():
        hall.sessions.sort(key=lambda x: (x.date, x.start, x.end))

    if no_records:
        lo, hi = recurring_window(today)
        return AdapterResult(
            covers_from=lo.isoformat(), covers_to=hi.isoformat(), source_updated=None,
            halls=halls, warnings=warnings,
            empty_reason="The booking export lists no public-skating times ('No records to display')",
        )
    return AdapterResult(
        covers_from=today.isoformat(),
        covers_to=max(dates + [today]).isoformat(),
        source_updated=None,
        halls=halls,
        warnings=warnings,
    )
