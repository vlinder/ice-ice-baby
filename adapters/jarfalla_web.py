"""Järfälla kommun: jarfalla.se "Åka skridskor"

Page layout (as of Nov 2026), inside <main>, a stack of Sitevision text portlets:

    <p>Ishallarna stängda för säsongen 2025/26. Återkommer i september ...</p>   (stale, ignored)
    <h3>Allmänhetens tider - Järfälla ishall</h3>
    <p>Att åka skridskor ... </p><p><a>Regler ...</a></p>
    <p><strong>Säsongen 2026/2027 (26 oktober - 20 december)</strong><br>
       Tider 21 december och framåt kommer.<br><br>
       Måndag 14.00-16.00 Med klubbor<br> ... Söndag 12.00-14.00 Utan klubbor</p>
    <h3>Allmänhetens åkning Järfälla ishall Höstlovet Vecka 44 (26/10 - 1/11</h3>
    <p><strong>Måndag 26/10</strong><br>12.00–14.55 Med klubba<br>15.05–18.00 Utan klubba</p> ...
    (the same two sections again for Kallhälls ishall; the holiday heading says "Kallhäll ishall")

The "Allmänhetens tider - <hall>" section is a weekly template valid for the season
dates in its "Säsongen" line; it is expanded over `recurring_window`. A dated holiday
block ("... Vecka N (d/m - d/m)") replaces the template on the dates it covers; blocks
that are entirely in the past are skipped unparsed. A dated block that starts right
after the recurring window extends that hall's range; `covers` is shared by both halls,
so it only reaches as far as every hall is described.

"Tider 21 december och framåt kommer" means the page says nothing about later dates,
so once the season in the "Säsongen" line is over we raise ParseError rather than claim
the halls are closed.

Every time line carries its own "Med/Utan klubb(a|or)" wording; there is no default.
The page has no "senast uppdaterad" stamp, so source_updated is None.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from bs4 import BeautifulSoup, Tag

from .common import (
    STICK_UNKNOWN,
    TIME_RANGE_RE, AdapterResult, BadTimeRange, HallResult, ParseError, Session, Slot,
    dm_date, expand_weekly, http_get, month_number, norm_space, recurring_window, slugify,
    stick_from_text, sv_date, time_ranges, weekday_number,
)

NAME = "jarfalla-web"
MUNICIPALITY = "Järfälla"
SOURCE_URL = "https://www.jarfalla.se/fritidochkultur/idrottochmotion/akaskridskor"

# hall name -> pattern matching it in headings ("Kallhäll ishall" appears in one heading)
HALLS = {
    "Järfälla ishall": re.compile(r"järfälla\s+ishall", re.I),
    "Kallhälls ishall": re.compile(r"kallhälls?\s+ishall", re.I),
}

RECURRING_RE = re.compile(r"^allmänhetens\s+tider\s*[-–]\s*(.+)$", re.I)
BLOCK_RE = re.compile(r"^allmänhetens\s+åkning\b", re.I)
BLOCK_RANGE_RE = re.compile(r"\(\s*(\d{1,2})/(\d{1,2})\s*[-–]\s*(\d{1,2})/(\d{1,2})\s*\)?")
SEASON_RE = re.compile(
    r"säsongen\s+(\d{4})\s*/\s*(\d{4})\s*\(\s*(\d{1,2})\s+([a-zåäö]+)\s*[-–]\s*(\d{1,2})\s+([a-zåäö]+)\s*\)",
    re.I,
)
WEEKLY_LINE_RE = re.compile(r"^([A-Za-zåäöÅÄÖ]+)\s+(.*)$")
DAY_HEADER_RE = re.compile(r"^([A-Za-zåäöÅÄÖ]+)\s+(\d{1,2})/(\d{1,2})$")
DIGIT_RE = re.compile(r"\d")


def fetch() -> dict[str, str]:
    return {"page": http_get(SOURCE_URL)}


def hall_id(name: str) -> str:
    return "jarfalla-" + slugify(name)


def _which_hall(text: str) -> str | None:
    found = [name for name, rx in HALLS.items() if rx.search(text)]
    return found[0] if len(found) == 1 else None


def _section_lines(h3: Tag) -> list[str]:
    """Text lines of the elements that follow a heading, up to the next heading."""
    lines: list[str] = []
    for sib in h3.find_next_siblings():
        if sib.name in ("h1", "h2", "h3"):
            break
        for br in sib.find_all("br"):
            br.replace_with("\n")
        lines += [norm_space(x) for x in sib.get_text("").split("\n")]
    return [x for x in lines if x]


def _stick(text: str) -> tuple[str, str | None]:
    s = stick_from_text(text)
    if s is None:
        return STICK_UNKNOWN, (text or None)
    return s, None


def _merge_covers(ranges: list[tuple[date, date]]) -> tuple[date, date] | None:
    """The first contiguous run of the given ranges (earliest start)."""
    if not ranges:
        return None
    ranges = sorted(ranges)
    lo, hi = ranges[0]
    for a, b in ranges[1:]:
        if a <= hi + timedelta(days=1):
            hi = max(hi, b)
        else:
            break
    return lo, hi


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    soup = BeautifulSoup(raw["page"], "lxml")
    root = soup.find("main") or soup.body or soup
    warnings: list[str] = []
    sm = SEASON_RE.search(norm_space(root.get_text(" ")))
    season_years = (int(sm.group(1)), int(sm.group(2))) if sm else None

    templates: dict[str, tuple[list[Slot], date, date]] = {}
    # hall -> list of (block_from, block_to, {date: [Session]})
    blocks: dict[str, list[tuple[date, date, dict[date, list[Session]]]]] = {}

    for h3 in root.find_all("h3"):
        heading = norm_space(h3.get_text(" "))
        m = RECURRING_RE.match(heading)
        if m:
            hall = _which_hall(m.group(1))
            if hall is None:
                raise ParseError(f"Unknown hall in heading {heading!r}")
            if hall in templates:
                raise ParseError(f"Two weekly schedules for {hall}")
            templates[hall] = _parse_recurring(hall, _section_lines(h3), warnings)
            continue
        if BLOCK_RE.match(heading):
            hall = _which_hall(heading)
            if hall is None:
                raise ParseError(f"Unknown hall in heading {heading!r}")
            rm = BLOCK_RANGE_RE.search(heading)
            if not rm:
                raise ParseError(f"No date range in heading {heading!r}")
            d1, m1, d2, m2 = rm.groups()
            b_from, b_to = _dm(d1, m1, season_years, today), _dm(d2, m2, season_years, today)
            if b_to < b_from:
                raise ParseError(f"Bad date range in heading {heading!r}")
            if b_to < today:
                continue  # past holiday block
            days = _parse_block(hall, _section_lines(h3), b_from, b_to, warnings)
            blocks.setdefault(hall, []).append((b_from, b_to, days))

    missing = [h for h in HALLS if h not in templates]
    if missing:
        raise ParseError(f"Weekly schedule missing for {missing} - has the page layout changed?")

    # covers: per hall the recurring window, extended by adjoining future dated blocks
    hall_covers: dict[str, tuple[date, date] | None] = {}
    for hall, (_slots, s_from, s_to) in templates.items():
        ranges = []
        win = recurring_window(today, s_from, s_to)
        if win:
            ranges.append(win)
        for b_from, b_to, _days in blocks.get(hall, []):
            ranges.append((max(b_from, today), b_to))
        hall_covers[hall] = _merge_covers(ranges)

    if any(c is None for c in hall_covers.values()):
        if all(c is None for c in hall_covers.values()) and all(
                s_from > today for _sl, s_from, _st in templates.values()):
            win = recurring_window(today)
            halls = {hall_id(h): HallResult(hall_id=hall_id(h), name=h, rink=None) for h in HALLS}
            return AdapterResult(
                covers_from=win[0].isoformat(), covers_to=win[1].isoformat(),
                source_updated=None, halls=halls, warnings=warnings,
                empty_reason="Season has not started yet",
            )
        over = [h for h, c in hall_covers.items() if c is None]
        raise ParseError(f"Stated season is over for {over} and the page has no later times yet")

    # covers is shared by all halls, so only claim what every hall's section describes
    lo = min(c[0] for c in hall_covers.values())
    hi = min(c[1] for c in hall_covers.values())

    halls: dict[str, HallResult] = {}
    for hall, (slots, s_from, s_to) in templates.items():
        hid = hall_id(hall)
        hr = HallResult(hall_id=hid, name=hall, rink=None)
        cov = hall_covers[hall]
        overridden: dict[date, list[Session]] = {}
        for b_from, b_to, days in blocks.get(hall, []):
            d = b_from
            while d <= b_to:
                overridden[d] = days.get(d, [])
                d += timedelta(days=1)
        win = recurring_window(today, s_from, s_to)
        sessions: list[Session] = []
        if win:
            sessions += expand_weekly(slots, win[0], win[1], SOURCE_URL, skip=set(overridden))
        for d, ss in overridden.items():
            if cov[0] <= d <= cov[1]:
                sessions += ss
        hr.sessions = _dedupe([x for x in sessions if lo.isoformat() <= x.date <= hi.isoformat()])
        halls[hid] = hr

    return AdapterResult(
        covers_from=lo.isoformat(), covers_to=hi.isoformat(),
        source_updated=None, halls=halls, warnings=warnings,
    )


def _dedupe(sessions: list[Session]) -> list[Session]:
    seen, out = set(), []
    for s in sorted(sessions, key=lambda x: (x.date, x.start, x.end, x.stick)):
        k = (s.date, s.start, s.end, s.stick)
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def _parse_recurring(hall: str, lines: list[str], warnings: list[str]) -> tuple[list[Slot], date, date]:
    season: tuple[date, date] | None = None
    slots: list[Slot] = []
    for line in lines:
        sm = SEASON_RE.search(line)
        if sm:
            y1, y2, d1, mo1, d2, mo2 = sm.groups()
            a = sv_date(d1, mo1, date(int(y1), 7, 1), year=y1 if month_number(mo1) >= 7 else y2)
            b = sv_date(d2, mo2, date(int(y1), 7, 1), year=y1 if month_number(mo2) >= 7 else y2)
            if b < a:
                raise ParseError(f"{hall}: season ends before it starts: {line!r}")
            season = (a, b)
            continue
        wm = WEEKLY_LINE_RE.match(line)
        wd = weekday_number(wm.group(1)) if wm else None
        if wd is None:
            # "Tider 21 december och framåt kommer.", rules link, prices: not schedule lines
            try:
                if time_ranges(line):
                    warnings.append(f"{hall}: unexpected line {line!r}")
            except BadTimeRange:
                warnings.append(f"{hall}: unexpected line {line!r}")
            continue
        rest = wm.group(2)
        try:
            ranges = time_ranges(rest)
        except BadTimeRange as exc:
            warnings.append(f"{hall}: could not read {line!r} ({exc})")
            continue
        if len(ranges) != 1:
            warnings.append(f"{hall}: could not read {line!r}")
            continue
        start, end = ranges[0]
        stick, note = _stick(_after_times(rest))
        slots.append(Slot(weekday=wd, start=start, end=end, stick=stick, note=note))
    if season is None:
        raise ParseError(f"{hall}: no 'Säsongen YYYY/YYYY (d month - d month)' line")
    if not slots:
        raise ParseError(f"{hall}: weekly schedule has no times")
    return slots, season[0], season[1]


def _dm(day: str, month: str, season_years: tuple[int, int] | None, today: date) -> date:
    """'26', '10' -> date. Within a stated season "2026/2027", July-December belong to
    the first year and January-June to the second; otherwise infer from today."""
    if season_years is None:
        return dm_date(day, month, today)
    y = season_years[0] if int(month) >= 7 else season_years[1]
    try:
        return date(y, int(month), int(day))
    except ValueError:
        raise ParseError(f"Invalid date {day}/{month}") from None


def _after_times(text: str) -> str:
    """The wording after the last time range on a line ("Med klubbor")."""
    last = None
    for last in TIME_RANGE_RE.finditer(text):
        pass
    return norm_space(text[last.end():] if last else text).strip(" ,.-–")


def _parse_block(hall: str, lines: list[str], b_from: date, b_to: date,
                 warnings: list[str]) -> dict[date, list[Session]]:
    days: dict[date, list[Session]] = {}
    current: date | None = None
    for line in lines:
        dm = DAY_HEADER_RE.match(line)
        if dm and weekday_number(dm.group(1)) is not None:
            wd = weekday_number(dm.group(1))
            d = _dm(dm.group(2), dm.group(3), (b_from.year, b_to.year), b_from)
            if not (b_from <= d <= b_to):
                warnings.append(f"{hall}: date {line!r} outside block {b_from}..{b_to}")
                current = None
                continue
            if d.weekday() != wd:
                warnings.append(f"{hall}: weekday does not match date in {line!r}")
                current = None
                continue
            current = d
            days.setdefault(d, [])
            continue
        if not DIGIT_RE.search(line):
            continue
        try:
            ranges = time_ranges(line)
        except BadTimeRange as exc:
            warnings.append(f"{hall}: could not read {line!r} ({exc})")
            continue
        if not ranges or current is None:
            warnings.append(f"{hall}: unexpected line {line!r}")
            continue
        stick, note = _stick(_after_times(line))
        for s, e in ranges:
            days[current].append(Session(date=current.isoformat(), start=s, end=e,
                                         stick=stick, source_url=SOURCE_URL, note=note))
    return days
