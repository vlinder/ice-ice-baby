"""Värmdö: the municipality's "Allmänhetens åkning" page for Ekvallens IP (Gustavsberg)

Page layout (as of Nov 2026), inside <main>:

    <h1>Allmänhetens åkning</h1>
    <ul><li><a href="...hostlovet2026...">OBS klicka här för tider höstlovet 2026</a></li></ul>
    <p>Bandyplanen: Stängd för säsongen 2026/2027<br>
       Ekrinken: Öppen för säsongen 2026/2027<br>
       Ishallen: Ingen allmänhetens åkning.<br> ...</p>
    <h2><a aria-controls="X">Tider Ekrinken (uterinken)</a></h2>      collapsible
    <div id="X">
      <p><strong>Öppnad för säsongen 2026/2027</strong></p>
      <p><strong>(Halva plan med klubbor enligt skyltning)</strong></p>
      <p>Måndag: 08:30–15:00 <br>... <br>Söndag: -</p>
      <p>Sidan uppdaterad 2026-10-19</p>
      <p>Isvård måndag-fredag mellan kl 11:30-12.00 samt kan komma att ske under övrig tid.</p>
    </div>

Only Ekrinken (the outdoor rink) has public skating, so it is the only hall
emitted. The status lines for Bandyplanen and Ishallen are read only to notice
if one of them opens (-> warning, as the adapter does not know where its times
would be). If Ekrinken's status says "Stängd", the hall is emitted with no
sessions and `empty_reason` set.

The weekday list is a recurring template, expanded over `recurring_window(today)`
(the page gives no season end). Stick: the section-wide "(Halva plan med klubbor
enligt skyltning)" makes every time `shared` unless a line has its own wording.
The ice-maintenance line ("Isvård måndag-fredag mellan kl 11:30-12.00") is not
split out of the sessions; it is attached as a note to the sessions it overlaps.

Holiday times (höstlov, jullov, sportlov) live on separate pages linked from the
top of this page. They are not fetched: a link to a holiday that is already over
(höstlov = ISO week 44) is ignored, any other holiday link becomes a warning so a
human can check whether the template is overridden.
"""
from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup, Tag

from .common import (
    STICK_UNKNOWN, AdapterResult, BadTimeRange, HallResult, ParseError, Slot,
    expand_weekly, http_get, iso_week_sunday, norm_space, recurring_window,
    stick_from_text, time_ranges, weekday_number,
)

NAME = "varmdo-web"
MUNICIPALITY = "Värmdö"
SOURCE_URL = ("https://www.varmdo.se/upplevaochgora/naturochfriluftsliv/alltomnaturochfriluftsliv/"
              "skridskoakningivarmdo/allmanhetensakning.4.3359b17e170bff933b54128d.html")

FACILITY = "Ekvallens IP"
RINK = "Ekrinken"
HALL_ID = "varmdo-ekvallens-ip-ekrinken"

SECTION_RE = re.compile(r"^Tider\s+Ekrinken\b", re.I)
STATUS_RE = re.compile(r"^(Bandyplanen|Ekrinken|Ishallen)\s*:\s*(.+)$", re.I)
STATUS_CLOSED_RE = re.compile(r"stängd|stängt|ingen\s+allmänhetens\s+åkning", re.I)
DAY_LINE_RE = re.compile(r"^([A-Za-zÅÄÖåäö]+)\s*:\s*(.*)$")
DAY_CLOSED_RE = re.compile(r"^(-|–|stängt|stängd|inställt|inställd|ingen\s+åkning)?\.?$", re.I)
UPDATED_RE = re.compile(r"uppdaterad\s+(\d{4})-(\d{2})-(\d{2})", re.I)
ISVARD_RE = re.compile(r"^Isvård\s+([a-zåäö]+)\s*[-–]\s*([a-zåäö]+)\s+mellan\s+kl\.?\s*(.+)$", re.I)
HOLIDAY_RE = re.compile(r"\b(höst|jul|sport|påsk|sommar|vinter)lov\w*(?:\s+(\d{4}))?", re.I)
TIMEISH_RE = re.compile(r"\d{1,2}[.:]\d{2}")


def fetch() -> dict[str, str]:
    return {"page": http_get(SOURCE_URL)}


def _lines(el: Tag) -> list[str]:
    for br in el.find_all("br"):
        br.replace_with("\n")
    out = []
    for block in el.find_all(["p", "li", "h3", "h4"]) or [el]:
        for part in block.get_text("").split("\n"):
            t = norm_space(part)
            if t:
                out.append(t)
    return out


def _section(main: Tag) -> Tag:
    for h in main.find_all(["h2", "h3"]):
        if SECTION_RE.search(norm_space(h.get_text())):
            a = h.find(attrs={"aria-controls": True})
            target = main.find(id=a["aria-controls"]) if a else None
            if target is None:
                target = h.find_next_sibling()
            if target is None:
                raise ParseError("Ekrinken section has no content")
            return target
    raise ParseError("No 'Tider Ekrinken' section found – has the page layout changed?")


def _holiday_past(word: str, year: str | None, today: date) -> bool:
    if word.lower() != "höst":
        return False
    y = int(year) if year else today.year
    return today > iso_week_sunday(y, 44)


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    soup = BeautifulSoup(raw["page"], "lxml")
    main = soup.find("main")
    if main is None:
        raise ParseError("Page has no <main>")
    section = _section(main)
    sec_lines = _lines(section)
    section.extract()  # the rest of <main> is the intro: status lines and links
    intro = _lines(main)

    warnings: list[str] = []
    rink_closed = False
    for line in intro:
        m = STATUS_RE.match(line)
        if m:
            place, status = m.group(1).capitalize(), m.group(2)
            closed = bool(STATUS_CLOSED_RE.search(status))
            if place == RINK:
                rink_closed = closed
            elif not closed:
                warnings.append(f"{place} may have public skating now (not read): {line!r}")
            continue
        h = HOLIDAY_RE.search(line)
        if h:
            if not _holiday_past(h.group(1), h.group(2), today):
                warnings.append(f"holiday times on a separate page are not read: {line!r}")
            continue
        if TIMEISH_RE.search(line):
            warnings.append(f"could not read {line!r}")

    default_stick: str | None = None
    updated: str | None = None
    slots: list[Slot] = []
    upkeep: list[tuple[int, int, str, str]] = []  # isvård: weekday range + time
    day_lines = 0
    for line in sec_lines:
        m = UPDATED_RE.search(line)
        if m:
            updated = f"{m.group(1)}-{m.group(2)}-{m.group(3)}T00:00"
            continue
        m = ISVARD_RE.match(line)
        if m:
            w1, w2 = weekday_number(m.group(1)), weekday_number(m.group(2))
            try:
                ranges = time_ranges(m.group(3))
            except BadTimeRange:
                ranges = []
            if w1 is None or w2 is None or len(ranges) != 1:
                warnings.append(f"could not read {line!r}")
            else:
                upkeep.append((w1, w2, *ranges[0]))
            continue
        m = DAY_LINE_RE.match(line)
        wd = weekday_number(m.group(1)) if m else None
        if wd is None:
            if re.match(r"\(?\s*(stängd|stängt)\b", line, re.I):
                rink_closed = True
                continue
            st = stick_from_text(line)
            if st and not TIMEISH_RE.search(line):
                default_stick = default_stick or st
            elif TIMEISH_RE.search(line):
                warnings.append(f"could not read {line!r}")
            continue
        day_lines += 1
        rest = m.group(2)
        try:
            ranges = time_ranges(rest)
        except BadTimeRange as exc:
            warnings.append(f"bad time in {line!r}: {exc}")
            continue
        if not ranges:
            if not DAY_CLOSED_RE.match(rest.strip()):
                warnings.append(f"could not read {line!r}")
            continue
        own = stick_from_text(rest)
        for s, e in ranges:
            if s < "05:00":
                warnings.append(f"time outside 05-24 in {line!r}")
                continue
            slot = Slot(weekday=wd, start=s, end=e, stick=own or default_stick or STICK_UNKNOWN)
            if slot not in slots:
                slots.append(slot)

    for slot in slots:
        for w1, w2, us, ue in upkeep:
            if w1 <= slot.weekday <= w2 and slot.start < ue and us < slot.end:
                slot.note = f"isvård {us}–{ue}"

    window = recurring_window(today)
    hall = HallResult(hall_id=HALL_ID, name=FACILITY, rink=RINK)
    empty_reason = None
    if rink_closed:
        empty_reason = "Ekrinken is closed according to the page"
        if slots:
            warnings.append("Ekrinken is marked closed but the page still lists times")
    elif not day_lines:
        raise ParseError("No weekday lines in the Ekrinken section – has the page layout changed?")
    else:
        hall.sessions = expand_weekly(slots, window[0], window[1], SOURCE_URL)

    return AdapterResult(
        covers_from=window[0].isoformat(),
        covers_to=window[1].isoformat(),
        source_updated=updated,
        halls={HALL_ID: hall},
        warnings=warnings,
        empty_reason=empty_reason,
    )
