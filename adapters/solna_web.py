"""Solna stad: solna.se, "Åka skridskor".

Page layout (as of Nov 2026): Sitevision page with "plusBox" accordions
(<section class="plusBox"> with <h2 class="plusBox__title"> and a
<div class="plusBox__body">). Two of them concern the halls at Ulriksdals IP:

1. "Ulriksdals IP - Höstlovet" – a dated list for a holiday:

       <h4>Lördag 24 oktober</h4>
       <p>Hall 2: 08.00-09.00 (utan klubba/puck)</p>
       <p>Hall 2: 14:30-15:30 (med klubba/puck)</p>

2. "Ishallarna på Ulriksdals IP" – the weekly schedule:

       <p><strong>Tiderna gäller från och med v. 45</strong></p>
       <h4>Hall 2</h4>
       <h4>Onsdag</h4>
       <p>12:00 - 13:30 (halva med klubba/puck, halva utan)</p>

The weekly schedule is expanded over `recurring_window` from the stated start
week ("till och med v. NN", if present, cuts it). A dated accordion is a
holiday override: every day from its first to its last listed date replaces
the weekly template for all halls (a day inside the span that is not listed has
no public skating). Dated accordions that ended before today are skipped.

Only halls the schedule names are emitted (in Nov 2026 only "Hall 2"; Hall 1
appears only in the past autumn-holiday list).

Two more accordions describe outdoor rinks in prose, Bergshamra IP and the
family rink "Frimärket" at Huvudstafältet:

    Isbanan på Bergshamra IP öppnar preliminärt till jullovet.
    Öppet alla dagar 10.00 – 22.00. ...
    Spel med klubbor och puck/boll är tillåtet mellan kl. 19.00 och 22.00.
    Utlåningens öppettider: Lördag: 10.00 - 15.00 ...      (skate lending: ignored)

While the text says the rink "öppnar" without a date, it is not open: the rink is
emitted with no sessions over the covered range. "öppnar <date>" opens it from
that date. Once it no longer says "öppnar", "Öppet alla dagar HH–HH" gives a
session every day; the hours the stick sentence names are "with_stick" and the
rest of the day "no_stick". An open rink without readable hours, or an outdoor
accordion for a rink the adapter does not know, gives a warning.

Stick wording is in parentheses after each time ("halva med klubba/puck, halva
utan" -> shared); there is no page-wide default.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from bs4 import BeautifulSoup, Tag

from .common import (
    STICK_UNKNOWN, STICK_WITH, STICK_WITHOUT, TIME_RANGE_RE, AdapterResult, BadTimeRange, HallResult, ParseError,
    Session, Slot, expand_weekly, hhmm, http_get, iso_week_monday, iso_week_sunday, norm_space,
    recurring_window, slugify, stick_from_text, sv_date, time_ranges, week_year,
    weekday_number,
)

NAME = "solna-web"
MUNICIPALITY = "Solna"
SOURCE_URL = "https://www.solna.se/uppleva--gora/natur-och-friluftsliv/aka-skridskor"

FACILITY = "Ulriksdals IP"

FROM_WEEK_RE = re.compile(r"från\s+och\s+med\s+v(?:ecka|\.)?\s*(\d{1,2})", re.I)
TO_WEEK_RE = re.compile(r"till\s+och\s+med\s+v(?:ecka|\.)?\s*(\d{1,2})", re.I)
HALL_RE = re.compile(r"^hall\s*(\d+)$", re.I)
HALL_PREFIX_RE = re.compile(r"^hall\s*(\d+)\s*:\s*", re.I)
DATED_RE = re.compile(r"^([a-zåäö]+)\s+(\d{1,2})\s+([a-zåäö]+)(?:\s+(\d{4}))?$", re.I)
NONE_RE = re.compile(r"ingen\s+(allmän|åkning)|inställ|stängt|stängd", re.I)
OUTDOOR_RE = re.compile(r"bergshamra|frimärket|isbana", re.I)
# Outdoor rinks described in prose: title pattern -> (hall id, name, rink).
OUTDOOR_RINKS = [
    (re.compile(r"bergshamra", re.I), ("solna-bergshamra-ip", "Bergshamra IP", None)),
    (re.compile(r"frimärket", re.I), ("solna-huvudstafaltet-frimarket", "Huvudstafältet", "Frimärket")),
]
_MONTHS = r"(?:januari|februari|mars|april|maj|juni|juli|augusti|september|oktober|november|december)"
OPENS_RE = re.compile(r"\böppnar\b", re.I)
OPENS_ON_RE = re.compile(r"\böppnar\b[^.]*?\b(\d{1,2})\s+(" + _MONTHS + r")(?:\s+(\d{4}))?", re.I)
# Only wording about the season: "kan vara stängd vid mildväder" does not close an open rink.
CLOSED_SEASON_RE = re.compile(r"stängd\w*\s+för\s+säsongen|stängt\s+för\s+säsongen|säsongen\s+är\s+(slut|över)", re.I)
DAILY_RE = re.compile(r"öppet\s+alla\s+dagar\s*(?:kl\.?\s*)?(.*)", re.I)
STICK_HOURS_RE = re.compile(
    r"klubb\w*[^.]*?tillåt\w*\s+mellan\s+(?:kl\.?\s*)?(\d{1,2})[.:](\d{2})\s+och\s+(?:kl\.?\s*)?(\d{1,2})[.:](\d{2})",
    re.I)


def fetch() -> dict[str, str]:
    return {"page": http_get(SOURCE_URL)}


def hall_id(rink: str) -> str:
    return "solna-" + slugify(FACILITY) + "-" + slugify(rink)


def _title(section: Tag) -> str:
    h = section.select_one(".plusBox__title")
    if h is None:
        return ""
    for t in h.select(".plusBox__toggler"):
        t.extract()
    return norm_space(h.get_text(" "))


def _lines(body: Tag) -> list[str]:
    out = []
    for el in body.find_all(["h2", "h3", "h4", "h5", "p", "li"]):
        if el.find(["p", "li"]):
            continue  # container; its children are visited separately
        for br in el.find_all("br"):
            br.replace_with("\n")
        for part in el.get_text().split("\n"):
            t = norm_space(part)
            if t:
                out.append(t)
    return out


def _times(line: str, where: str, warnings: list[str]) -> tuple[str | None, list[tuple[str, str, str, str | None]]]:
    """Parse '[Hall 2: ]12:00 - 13:30 (halva med klubba/puck, halva utan)'.

    Returns (rink or None, [(start, end, stick, note)]); unreadable -> warning, []."""
    rink = None
    m = HALL_PREFIX_RE.match(line)
    if m:
        rink = f"Hall {int(m.group(1))}"
        line_rest = line[m.end():]
    else:
        line_rest = line
    try:
        ranges = time_ranges(line_rest)
    except BadTimeRange as exc:
        warnings.append(f"{where}: could not read {line!r} ({exc})")
        return rink, []
    if not ranges:
        if not (NONE_RE.search(line_rest) and not re.search(r"\d", line_rest)):
            warnings.append(f"{where}: could not read {line!r}")
        return rink, []
    wording = norm_space(TIME_RANGE_RE.sub(" ", line_rest)).strip(" ()-–,.")
    stick = stick_from_text(wording)
    note = None
    if stick is None:
        stick, note = STICK_UNKNOWN, (wording or None)
    return rink, [(s, e, stick, note) for s, e in ranges]


def _source_updated(soup: BeautifulSoup) -> str | None:
    t = soup.select_one(".pageLastPublished time, .pageLastPublished__date")
    if t:
        m = re.search(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?", t.get_text())
        if m:
            return f"{m.group(1)}T{m.group(2) or '00:00'}"
    return None


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    soup = BeautifulSoup(raw["page"], "lxml")
    sections = soup.select("section.plusBox")
    warnings: list[str] = []

    # weekly template: rink -> slots; season limits from the heading
    weekly: dict[str, list[Slot]] = {}
    season: tuple[date, date | None] | None = None
    # dated overrides: list of (first date, last date, {date: [(rink, start, end, stick, note)]})
    dated: list[tuple[date, date, dict[date, list]]] = []
    outdoor: list[tuple[tuple[str, str, str | None], str, list[str]]] = []
    found = False

    for sec in sections:
        title = _title(sec)
        body = sec.select_one(".plusBox__body")
        if "ulriksdal" not in title.lower():
            if OUTDOOR_RE.search(title):
                known = next((v for pat, v in OUTDOOR_RINKS if pat.search(title)), None)
                if known is None or body is None:
                    warnings.append(f"Section {title!r} is not handled by the adapter")
                else:
                    outdoor.append((known, title, _lines(body)))
            continue
        if body is None:
            raise ParseError(f"Section {title!r} has no body")
        found = True
        lines = _lines(body)
        text = " ".join(lines)
        fm = FROM_WEEK_RE.search(text)
        if fm:
            if season is not None:
                raise ParseError("More than one weekly schedule for Ulriksdals IP")
            w = int(fm.group(1))
            s_from = iso_week_monday(week_year(w, today), w)
            s_to = None
            tm = TO_WEEK_RE.search(text)
            if tm:
                w2 = int(tm.group(1))
                y2 = s_from.isocalendar()[0] + (1 if w2 < w else 0)
                s_to = iso_week_sunday(y2, w2)
            season = (s_from, s_to)
            rink = None
            wd = None
            for line in lines:
                if FROM_WEEK_RE.search(line) or line.lower() == "allmänhetens åkning":
                    continue
                hm = HALL_RE.match(line)
                if hm:
                    rink, wd = f"Hall {int(hm.group(1))}", None
                    continue
                w_no = weekday_number(line)
                if w_no is not None:
                    wd = w_no
                    continue
                if not TIME_RANGE_RE.search(line) and not re.search(r"\d", line):
                    continue  # prose
                where = f"{title} {rink or '?'} weekday {wd}"
                r2, times = _times(line, where, warnings)
                r2 = r2 or rink
                if not times:
                    continue
                if wd is None or r2 is None:
                    warnings.append(f"{title}: time without hall/day heading: {line!r}")
                    continue
                for s, e, stick, note in times:
                    weekly.setdefault(r2, []).append(Slot(wd, s, e, stick, note))
            continue

        # dated holiday list
        per_day: dict[date, list] = {}
        day: date | None = None
        for line in lines:
            if line.lower() == "allmänhetens åkning":
                continue
            dm = DATED_RE.match(line)
            if dm and weekday_number(dm.group(1)) is not None:
                day = sv_date(dm.group(2), dm.group(3), today, dm.group(4))
                if day.weekday() != weekday_number(dm.group(1)):
                    warnings.append(f"{title}: weekday does not match date in {line!r}")
                per_day.setdefault(day, [])
                continue
            if not TIME_RANGE_RE.search(line) and not re.search(r"\d", line):
                continue
            if day is None:
                warnings.append(f"{title}: time without a date heading: {line!r}")
                continue
            if day < today:
                continue  # past day: no need to read it
            rink, times = _times(line, f"{title} {day}", warnings)
            if times and rink is None:
                warnings.append(f"{title} {day}: line without hall: {line!r}")
                continue
            for s, e, stick, note in times:
                per_day[day].append((rink, s, e, stick, note))
        if not per_day:
            raise ParseError(f"Section {title!r} has neither a weekly schedule nor dated days")
        dated.append((min(per_day), max(per_day), per_day))

    if not found:
        raise ParseError("No 'Ulriksdals IP' section found – layout changed?")

    window = recurring_window(today, *season) if season else None
    horizon = recurring_window(today)[1]
    spans = [window] if window else []
    for first, last, _ in dated:
        a, b = max(first, today), min(last, horizon)
        if a <= b:
            spans.append((a, b))
    if spans:
        lo, hi = min(x[0] for x in spans), max(x[1] for x in spans)
    elif season is not None:
        lo, hi = recurring_window(today)  # weekly schedule not in effect within the horizon
    else:
        raise ParseError("No weekly schedule and no current dated list for Ulriksdals IP")

    halls: dict[str, HallResult] = {}

    def add(rink: str, d: date, s: str, e: str, stick: str, note: str | None) -> None:
        hid = hall_id(rink)
        hall = halls.setdefault(hid, HallResult(hall_id=hid, name=FACILITY, rink=rink))
        if s < "05:00" or e > "24:00":
            warnings.append(f"{hid}: {d} {s}–{e} outside 05–24, skipped")
            return
        key = (d.isoformat(), s, e, stick)
        if any((x.date, x.start, x.end, x.stick) == key for x in hall.sessions):
            return
        hall.sessions.append(Session(date=d.isoformat(), start=s, end=e, stick=stick,
                                     source_url=SOURCE_URL, note=note))

    overridden: set[date] = set()
    for first, last, per_day in dated:
        d = max(first, lo)
        while d <= min(last, hi):
            overridden.add(d)
            for rink, s, e, stick, note in per_day.get(d, []):
                add(rink, d, s, e, stick, note)
            d += timedelta(days=1)
    if window:
        for rink, slots in weekly.items():
            halls.setdefault(hall_id(rink), HallResult(hall_id=hall_id(rink), name=FACILITY, rink=rink))
            for sess in expand_weekly(slots, window[0], window[1], SOURCE_URL, skip=overridden):
                add(rink, date.fromisoformat(sess.date), sess.start, sess.end, sess.stick, sess.note)

    for (hid, name, rink), title, lines in outdoor:
        hall = HallResult(hall_id=hid, name=name, rink=rink)
        hall.sessions = _outdoor_sessions(hid, lines, lo, hi, today, warnings)
        halls[hid] = hall

    for h in halls.values():
        h.sessions.sort(key=lambda s: (s.date, s.start, s.end))
    return AdapterResult(
        covers_from=lo.isoformat(),
        covers_to=hi.isoformat(),
        source_updated=_source_updated(soup),
        halls=halls,
        warnings=warnings,
    )


def _outdoor_sessions(hid: str, lines: list[str], lo: date, hi: date, today: date,
                      warnings: list[str]) -> list[Session]:
    """Daily sessions for an outdoor rink described in prose (see the module docstring)."""
    text = " ".join(lines)
    start = lo
    if OPENS_RE.search(text):
        m = OPENS_ON_RE.search(text)
        if not m:
            return []  # "öppnar preliminärt till jullovet": not open yet, no date given
        start = max(lo, sv_date(m.group(1), m.group(2), today, m.group(3)))
    elif CLOSED_SEASON_RE.search(text):
        return []
    daily = next((m.group(1) for m in map(DAILY_RE.search, lines) if m), None)
    try:
        ranges = time_ranges(daily) if daily else []
    except BadTimeRange:
        ranges = []
    if len(ranges) != 1:
        warnings.append(f"{hid}: no opening hours found in {(daily or text)[:100]!r}")
        return []
    s, e = ranges[0]
    parts = [(s, e, STICK_UNKNOWN)]
    m = STICK_HOURS_RE.search(text)
    if m:
        a, b = hhmm(m.group(1), m.group(2)), hhmm(m.group(3), m.group(4))
        if not (s <= a < b <= e):
            warnings.append(f"{hid}: stick hours {a}–{b} outside opening hours {s}–{e}")
            return []
        parts = [(x, y, k) for x, y, k in [(s, a, STICK_WITHOUT), (a, b, STICK_WITH), (b, e, STICK_WITHOUT)] if x < y]
    out = []
    d = start
    while d <= hi:
        out += [Session(date=d.isoformat(), start=x, end=y, stick=k, source_url=SOURCE_URL) for x, y, k in parts]
        d += timedelta(days=1)
    return out
