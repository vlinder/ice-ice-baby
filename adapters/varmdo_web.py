"""Värmdö: the municipality's "Allmänhetens åkning" page for Ekvallens IP (Gustavsberg)

Page layout (as of Nov 2026), inside <main>:

    <h1>Allmänhetens åkning</h1>
    <ul><li><a href="...hostlovet2026...">OBS klicka här för tider höstlovet 2026</a></li></ul>
    <p>Bandyplanen: Öppen för säsongen 2026/2027<br>
       Ekrinken: Öppen för säsongen 2026/2027<br>
       Ishallen: Ingen allmänhetens åkning.<br> ...</p>
    <h2><a aria-controls="B">Tider bandyplan</a></h2>                 collapsible, only in season
    <div id="B">
      <p><strong>(Halva plan med klubbor enligt skyltning)</strong></p>
      <p>Status: Öppen för säsongen 2026/2027</p>
      <p>Måndag: avstängd dagtid för isvård<br>... <br>
         Lördag: 8.30–9.30, 11.30–22.00; spolning 13.30–14.00, 17.30–18.00<br>...</p>
      <p>Sidan uppdaterad 2026-11-11</p>
      <p>Isvård måndag–fredag mellan kl 11.30–12.00, kan också ske under övrig tid.</p>
    </div>
    <h2><a aria-controls="X">Tider Ekrinken (uterinken)</a></h2>      collapsible
    <div id="X">
      <p><strong>Öppnad för säsongen 2026/2027</strong></p>
      <p><strong>(Halva plan med klubbor enligt skyltning)</strong></p>
      <p>Måndag: 08:30–15:00 <br>... <br>Söndag: -</p>
      <p>Sidan uppdaterad 2026-10-19</p>
      <p>Isvård måndag-fredag mellan kl 11:30-12.00 samt kan komma att ske under övrig tid.</p>
    </div>

Ekrinken (the outdoor rink) always has a section. Bandyplanen gets its own section
while it is open; each section becomes one hall. Ishallen has no public skating.
The status lines at the top are read to notice a place that opens without a section
of its own (-> warning, as the adapter does not know where its times would be). A
place whose status (or the section's own "Status:" line) says "Stängd" is emitted
with no sessions; if every emitted hall is closed, `empty_reason` is set.

The weekday lists are recurring templates, expanded over `recurring_window(today)`
(the page gives no season end). Stick: a section-wide "(Halva plan med klubbor
enligt skyltning)" makes every time in that section `shared` unless a line has its
own wording. A day that is "avstängd" (e.g. "avstängd dagtid för isvård") has no
times. Ice maintenance is cut out of the sessions (`split_around`): a session that
runs across the "Isvård måndag-fredag mellan kl ..." time or a day's "; spolning
13.30–14.00, ..." becomes one session before and one after, and the part before
gets a note such as "isvård 11:30–12:00".

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
    dm_date, expand_weekly, http_get, iso_week_sunday, norm_space, recurring_window,
    split_around, stick_from_text, time_ranges, weekday_number,
)

NAME = "varmdo-web"
MUNICIPALITY = "Värmdö"
SOURCE_URL = ("https://www.varmdo.se/upplevaochgora/naturochfriluftsliv/alltomnaturochfriluftsliv/"
              "skridskoakningivarmdo/allmanhetensakning.4.3359b17e170bff933b54128d.html")

FACILITY = "Ekvallens IP"
RINK = "Ekrinken"
HALL_ID = "varmdo-ekvallens-ip-ekrinken"
BANDY = "Bandyplanen"
BANDY_HALL_ID = "varmdo-ekvallens-ip-bandyplanen"

# place (as in the status lines) -> (section heading, hall id)
SECTIONS = {
    RINK: (re.compile(r"^(?:Tider|Åkning\s+på)\s+Ekrinken\b", re.I), HALL_ID),
    BANDY: (re.compile(r"^(?:Tider|Åkning\s+på)\s+bandyplan", re.I), BANDY_HALL_ID),
}
# "Bandyplanen: Öppen …" or, from Nov 2026, "Bandyplan (utan tak): Öppen", "Ekhallen: Ingen allmän åkning …"
STATUS_RE = re.compile(r"^(Bandyplan(?:en)?|Ekrinken|Ishallen|Ekhallen)\b[^:]{0,20}:\s*(.+)$", re.I)
PLACE_NAMES = {"bandyplan": BANDY, "bandyplanen": BANDY, "ekrinken": RINK,
               "ishallen": "Ishallen", "ekhallen": "Ishallen"}
SECTION_STATUS_RE = re.compile(r"^Status\s*:\s*(.+)$", re.I)
STATUS_CLOSED_RE = re.compile(r"stängd|stängt|ingen\s+allmän\w*\s+åkning", re.I)
DAY_LINE_RE = re.compile(r"^([A-Za-zÅÄÖåäö]+)\s*:\s*(.*)$")
DAY_CLOSED_RE = re.compile(r"^(-|–|stängt|stängd|inställt|inställd|ingen\s+åkning|avstängd\b.*)?\.?$", re.I)
SPOLNING_RE = re.compile(r"[;,]?\s*\bspolning\b\s*(.*)$", re.I)
UPDATED_RE = re.compile(r"uppdatera[dt]\s+(?:senast\s*:?\s*)?(\d{4})-(\d{2})-(\d{2})", re.I)
# "Isvård måndag-fredag mellan kl 11:30-12.00 …" or "… Isvård sker måndag-fredag klockan 11:30-12.00 …"
ISVARD_RE = re.compile(r"\bIsvård\s+(?:sker\s+)?([a-zåäö]+)\s*[-–]\s*([a-zåäö]+)\s+(?:mellan\s+)?"
                       r"(?:kl\.?|klockan)\s*(\d{1,2}[.:]\d{2}\s*[-–]\s*\d{1,2}[.:]\d{2})", re.I)
# "Lördag: 08.30-9.30 och 11.30-22.00 (isvård 13.30-14.30 och 17.30-18.00)"
DAY_ISVARD_RE = re.compile(r"\(\s*isvård\s+([^)]*)\)", re.I)
# "Måndag: 09.00-15.30 OBS 23/11 ingen åkning pga isvård"
DAY_CANCEL_RE = re.compile(r"\bOBS!?\s*(\d{1,2})/(\d{1,2})\s+(?:ingen\s+åkning|inställ\w*|stängt)[^;]*$", re.I)
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


def _section(main: Tag, heading: re.Pattern) -> Tag | None:
    for h in main.find_all(["h2", "h3"]):
        if heading.search(norm_space(h.get_text())):
            a = h.find(attrs={"aria-controls": True})
            target = main.find(id=a["aria-controls"]) if a else None
            if target is None:
                # From Nov 2026: a plain heading followed by its paragraphs, up to the next heading
                # of the same or a higher level. Move them into one element.
                target = Tag(name="div")
                for sib in list(h.find_next_siblings()):
                    if sib.name in ("h1", "h2") or (sib.name == h.name):
                        break
                    target.append(sib.extract())
            if not target.get_text(strip=True):
                raise ParseError(f"{norm_space(h.get_text())!r} section has no content")
            return target
    return None


def _holiday_past(word: str, year: str | None, today: date) -> bool:
    if word.lower() != "höst":
        return False
    y = int(year) if year else today.year
    return today > iso_week_sunday(y, 44)


def _read_section(lines: list[str], prefix: str, warnings: list[str], today: date,
                  page_stick: str | None = None):
    """One section's lines -> (slots, source_updated, number of weekday lines, closed, cancelled dates)."""
    default_stick: str | None = None
    cancelled: set[date] = set()
    updated: str | None = None
    closed = False
    slots: list[Slot] = []
    upkeep: list[tuple[int, int, str, str]] = []  # isvård: weekday range + time
    flooding: list[tuple[int, str, str, str]] = []  # spolning/isvård on one weekday: + time + kind
    day_lines = 0
    for line in lines:
        m = UPDATED_RE.search(line)
        if m:
            updated = f"{m.group(1)}-{m.group(2)}-{m.group(3)}T00:00"
            continue
        m = SECTION_STATUS_RE.match(line)
        if m:
            closed = closed or bool(STATUS_CLOSED_RE.search(m.group(1)))
            continue
        m = DAY_LINE_RE.match(line)
        wd = weekday_number(m.group(1)) if m else None
        im = ISVARD_RE.search(line) if wd is None else None
        if im:
            w1, w2 = weekday_number(im.group(1)), weekday_number(im.group(2))
            try:
                ranges = time_ranges(im.group(3))
            except BadTimeRange:
                ranges = []
            if w1 is None or w2 is None or len(ranges) != 1:
                warnings.append(f"{prefix}could not read {line!r}")
            else:
                upkeep.append((w1, w2, *ranges[0]))
            if TIMEISH_RE.search(ISVARD_RE.sub(" ", line)):
                warnings.append(f"{prefix}could not read {line!r}")
            continue
        if wd is None:
            if re.match(r"\(?\s*(stängd|stängt)\b", line, re.I):
                closed = True
                continue
            st = stick_from_text(line)
            if st and not TIMEISH_RE.search(line):
                default_stick = default_stick or st
            elif TIMEISH_RE.search(line):
                warnings.append(f"{prefix}could not read {line!r}")
            continue
        day_lines += 1
        rest = m.group(2)
        cm = DAY_CANCEL_RE.search(rest)
        if cm:
            try:
                cancelled.add(dm_date(cm.group(1), cm.group(2), today))
            except (ValueError, ParseError):
                warnings.append(f"{prefix}could not read {line!r}")
            rest = rest[:cm.start()]
        sp = SPOLNING_RE.search(rest) or DAY_ISVARD_RE.search(rest)
        try:
            if sp:
                floods = time_ranges(sp.group(1))
                if not floods:
                    warnings.append(f"{prefix}could not read {line!r}")
                    continue
                kind = "isvård" if sp.re is DAY_ISVARD_RE else "spolning"
                flooding.extend((wd, s, e, kind) for s, e in floods)
                rest = rest[:sp.start()] + rest[sp.end():]
            ranges = time_ranges(rest)
        except BadTimeRange as exc:
            warnings.append(f"{prefix}bad time in {line!r}: {exc}")
            continue
        if not ranges:
            if not DAY_CLOSED_RE.match(rest.strip()) or sp:
                warnings.append(f"{prefix}could not read {line!r}")
            continue
        own = stick_from_text(rest)
        for s, e in ranges:
            if s < "05:00":
                warnings.append(f"{prefix}time outside 05-24 in {line!r}")
                continue
            slot = Slot(weekday=wd, start=s, end=e, stick=own or default_stick or page_stick or STICK_UNKNOWN)
            if slot not in slots:
                slots.append(slot)

    split: list[Slot] = []
    for slot in slots:
        gaps = [(us, ue, f"isvård {us}–{ue}") for w1, w2, us, ue in upkeep
                if w1 <= slot.weekday <= w2]
        gaps += [(fs, fe, f"{kind} {fs}–{fe}") for w, fs, fe, kind in flooding if w == slot.weekday]
        split.extend(split_around(slot, gaps))
    return split, updated, day_lines, closed, cancelled


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    soup = BeautifulSoup(raw["page"], "lxml")
    main = soup.find("main")
    if main is None:
        raise ParseError("Page has no <main>")
    sections: dict[str, list[str]] = {}
    for place, (heading, _) in SECTIONS.items():
        el = _section(main, heading)
        if el is not None:
            sections[place] = _lines(el)
            el.extract()  # the rest of <main> is the intro: status lines and links
    if RINK not in sections:
        raise ParseError("No Ekrinken section ('Tider Ekrinken' or 'Åkning på Ekrinken') found – has the page layout changed?")
    intro = _lines(main)

    warnings: list[str] = []
    closed = {place: False for place in sections}
    page_stick: str | None = None
    for line in intro:
        m = STATUS_RE.match(line)
        if m:
            place, status = PLACE_NAMES[m.group(1).lower()], m.group(2)
            is_closed = bool(STATUS_CLOSED_RE.search(status))
            if place in sections:
                closed[place] = is_closed
            elif not is_closed:
                warnings.append(f"{place} may have public skating now (not read): {line!r}")
            continue
        h = HOLIDAY_RE.search(line)
        if h:
            if not _holiday_past(h.group(1), h.group(2), today):
                warnings.append(f"holiday times on a separate page are not read: {line!r}")
            continue
        if TIMEISH_RE.search(line):
            warnings.append(f"could not read {line!r}")
            continue
        # "Spel med klubba tillåts på halva planen enligt skyltning." (page-wide rule, Nov 2026)
        page_stick = page_stick or stick_from_text(line)

    window = recurring_window(today)
    halls: dict[str, HallResult] = {}
    updated: str | None = None
    for place, lines in sections.items():
        hall_id = SECTIONS[place][1]
        # Ekrinken's warnings keep their old, hall-less form (it used to be the only hall).
        prefix = "" if place == RINK else f"{hall_id}: "
        slots, upd, day_lines, sec_closed, cancelled = _read_section(lines, prefix, warnings, today, page_stick)
        if upd and (updated is None or upd > updated):
            updated = upd
        hall = HallResult(hall_id=hall_id, name=FACILITY, rink=place)
        if closed[place] or sec_closed:
            closed[place] = True
            if slots:
                warnings.append(f"{prefix}{place} is marked closed but the page still lists times")
        elif not day_lines:
            raise ParseError(f"No weekday lines in the {place} section – has the page layout changed?")
        else:
            hall.sessions = expand_weekly(slots, window[0], window[1], SOURCE_URL, skip=cancelled)
        halls[hall_id] = hall

    empty_reason = None
    if all(closed.values()):
        empty_reason = "Ekrinken is closed according to the page"
        if BANDY in closed:
            empty_reason = "Ekrinken and Bandyplanen are closed according to the page"

    return AdapterResult(
        covers_from=window[0].isoformat(),
        covers_to=window[1].isoformat(),
        source_updated=updated,
        halls=halls,
        warnings=warnings,
        empty_reason=empty_reason,
    )
