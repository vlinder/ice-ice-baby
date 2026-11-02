"""Upplands Väsby: the municipality's "Ishallar" page (Vilundaparken)

Page layout (as of Nov 2026): a Sitevision page whose <main> holds a weekly
template as a table:

    <table>
      <caption>Allmänhetens åkning (Ishall B), från vecka 44</caption>
      <thead><tr><th>Dag</th><th>Tid</th><th>Klubba</th></tr></thead>
      <tbody>
        <tr><td>Måndag</td><td>14.00-15.45</td><td>Klubba tillåten</td></tr>
        <tr><td>Fredag</td><td>Stängt</td><td> </td></tr>
        <tr><td>Lördag förmiddag</td><td>11.00-13.45</td><td>Klubba ej tillåten</td></tr>
        <tr><td>Lördag kväll</td><td>18.30-20.45</td><td>Klubba tillåten</td></tr> ...

The caption names the rink ("Ishall B" = the unheated tent hall; Ishall A has no
public skating listed) and when the template starts ("från vecka 44"; an optional
"till/t.o.m. vecka N" is honoured too). Each table with such a caption is expanded
with `expand_weekly` over `recurring_window(today, season start, season end)`.
The day cell may carry a suffix ("förmiddag", "kväll"); only the weekday is used.
Stick comes from the Klubba column per row; there is no page-wide default.

The page has no section for holiday/deviating times. Any other table, or a line
outside the table that looks like a time or mentions closures/holidays, becomes a
warning so a human can check it. "Senast uppdaterad: 27 oktober 2026" at the
bottom is `source_updated`.
"""
from __future__ import annotations

import copy
import re
from datetime import date

from bs4 import BeautifulSoup, Tag

from .common import (
    STICK_UNKNOWN, AdapterResult, BadTimeRange, HallResult, ParseError, Slot,
    expand_weekly, http_get, iso_week_monday, iso_week_sunday, norm_space,
    recurring_window, slugify, stick_from_text, sv_date, time_ranges, weekday_number,
    week_year,
)

NAME = "upplands-vasby-web"
MUNICIPALITY = "Upplands Väsby"
SOURCE_URL = "https://www.upplandsvasby.se/uppleva-och-gora/idrott-motion-och-friluftsliv/ishallar"

FACILITY = "Vilundaparkens ishall"

CAPTION_RE = re.compile(r"allmänhetens\s+åkning", re.I)
RINK_RE = re.compile(r"\(\s*Ishall\s+([A-Z])\s*\)", re.I)
FROM_WEEK_RE = re.compile(r"från\s+(?:och\s+med\s+)?(?:v\.?|vecka)\s*(\d{1,2})", re.I)
TO_WEEK_RE = re.compile(r"(?:till|t\.\s*o\.\s*m\.?|tom)\s+(?:och\s+med\s+)?(?:v\.?|vecka)\s*(\d{1,2})", re.I)
CLOSED_RE = re.compile(r"^(stängt|stängd|inställt|inställd|ingen\s+åkning|-|–)$", re.I)
UPDATED_RE = re.compile(r"Senast\s+uppdaterad:?\s*(\d{1,2})\s+([a-zåäö]+)\s+(\d{4})", re.I)
# Lines outside the template that may describe times we would otherwise miss.
SUSPICIOUS_RE = re.compile(
    r"\d{1,2}[.:]\d{2}\s*[-–]\s*\d{1,2}[.:]?\d{0,2}"
    r"|\b(stängt|stängd|inställ\w*|avvikande)\b"
    r"|\b(höst|jul|sport|påsk|sommar|vinter)lov"
    r"|\b(julafton|juldag|nyårsafton|nyårsdag|trettondag|helgdag)",
    re.I,
)
EXPECTED_HEADERS = ["dag", "tid", "klubba"]


def fetch() -> dict[str, str]:
    return {"page": http_get(SOURCE_URL)}


def hall_id(rink_letter: str) -> str:
    return "upplands-vasby-" + slugify(f"{FACILITY} {rink_letter}")


def _season(caption: str, today: date) -> tuple[date | None, date | None]:
    """Season start/end from the caption. Unrecognised period wording -> ParseError."""
    rest = RINK_RE.sub("", caption)
    lo = hi = None
    m = FROM_WEEK_RE.search(rest)
    if m:
        w = int(m.group(1))
        lo = iso_week_monday(week_year(w, today), w)
        rest = rest.replace(m.group(0), "")
    m = TO_WEEK_RE.search(rest)
    if m:
        w = int(m.group(1))
        y = week_year(w, today)
        if lo and iso_week_sunday(y, w) < lo:
            y += 1  # "från vecka 44 till vecka 10" spans the new year
        hi = iso_week_sunday(y, w)
        rest = rest.replace(m.group(0), "")
    if re.search(r"\d", rest):
        raise ParseError(f"Unrecognised period in caption {caption!r}")
    return lo, hi


def _parse_table(table: Tag, caption: str, warnings: list[str]) -> tuple[str, str, list[Slot]]:
    m = RINK_RE.search(caption)
    if not m:
        raise ParseError(f"No 'Ishall X' in caption {caption!r}")
    letter = m.group(1).upper()
    hid = hall_id(letter)
    headers = [norm_space(th.get_text()).lower() for th in table.select("thead th")]
    if headers != EXPECTED_HEADERS:
        raise ParseError(f"Unexpected table headers {headers!r}")
    slots: list[Slot] = []
    rows = table.select("tbody tr")
    if not rows:
        raise ParseError(f"Empty schedule table {caption!r}")
    for tr in rows:
        cells = [norm_space(td.get_text(" ")) for td in tr.find_all(["td", "th"])]
        line = " | ".join(cells)
        if len(cells) != 3:
            warnings.append(f"{hid}: unexpected row {line!r}")
            continue
        day, tid, klubba = cells
        wd = weekday_number(day.split(" ")[0]) if day else None
        if wd is None:
            warnings.append(f"{hid}: unknown weekday in {line!r}")
            continue
        try:
            ranges = time_ranges(tid)
        except BadTimeRange as exc:
            warnings.append(f"{hid}: bad time in {line!r}: {exc}")
            continue
        if not ranges:
            if not CLOSED_RE.match(tid):
                warnings.append(f"{hid}: could not read {line!r}")
            continue
        stick = stick_from_text(klubba)
        note = None
        if stick is None:
            stick, note = STICK_UNKNOWN, (klubba or None)
        for s, e in ranges:
            if s < "05:00":
                warnings.append(f"{hid}: time outside 05-24 in {line!r}")
                continue
            slot = Slot(weekday=wd, start=s, end=e, stick=stick, note=note)
            if slot not in slots:
                slots.append(slot)
    return hid, f"Ishall {letter}", slots


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    soup = BeautifulSoup(raw["page"], "lxml")
    main = soup.find("main")
    if main is None:
        raise ParseError("Page has no <main>")

    warnings: list[str] = []
    halls: dict[str, HallResult] = {}
    windows: list[tuple[date, date]] = []
    found = False
    for table in main.find_all("table"):
        cap_el = table.find("caption")
        caption = norm_space(cap_el.get_text()) if cap_el else ""
        if not CAPTION_RE.search(caption):
            warnings.append(f"unrecognised table on page: {norm_space(table.get_text(' '))[:120]!r}")
            continue
        found = True
        season_from, season_to = _season(caption, today)
        hid, rink, slots = _parse_table(table, caption, warnings)
        hall = halls.setdefault(hid, HallResult(hall_id=hid, name=FACILITY, rink=rink))
        window = recurring_window(today, season_from, season_to)
        if window is None:
            warnings.append(f"{hid}: template {caption!r} is not valid within the next weeks")
            continue
        windows.append(window)
        hall.sessions += expand_weekly(slots, window[0], window[1], SOURCE_URL)

    if not found:
        raise ParseError("No 'Allmänhetens åkning' table found – has the page layout changed?")

    for hall in halls.values():
        uniq = {(x.date, x.start, x.end, x.stick): x for x in hall.sessions}
        hall.sessions = sorted(uniq.values(), key=lambda x: (x.date, x.start, x.end))

    # Text outside the schedule table(s) that might hold extra times or closures.
    rest = copy.copy(main)
    for t in rest.find_all("table"):
        t.decompose()
    for br in rest.find_all("br"):
        br.replace_with("\n")
    lines = [norm_space(x) for x in rest.get_text("\n").split("\n")]
    updated = None
    for line in lines:
        if not line:
            continue
        if SUSPICIOUS_RE.search(line):
            warnings.append(f"unhandled text that may affect the schedule: {line!r}")
    m = UPDATED_RE.search(" ".join(lines))
    if m:
        updated = sv_date(m.group(1), m.group(2), today, m.group(3)).isoformat() + "T00:00"

    if not windows:
        window = recurring_window(today)
        windows.append(window)
    return AdapterResult(
        covers_from=min(w[0] for w in windows).isoformat(),
        covers_to=max(w[1] for w in windows).isoformat(),
        source_updated=updated,
        halls=halls,
        warnings=warnings,
    )
