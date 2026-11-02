"""Sigtuna kommun: sigtuna.se "Ishall" (Pinbackshallen, Märsta).

Page layout (as of Nov 2026, Sitevision): under ``<h2>Allmänhetens åkning</h2>``
come one or more schedule blocks, each an ``<h3>`` followed by one
``<h4>A-hallen</h4>`` / ``<h4>B-hallen</h4>`` sub-block per rink:

* holiday blocks: ``<h3>Öppettider … under höstlovet den 26 oktober till 1
  november 2026</h3>``, tables Dag | Tid with dated rows ("Måndag 26 oktober");
* the ordinary block: ``<h3>Ordinarie öppettider för allmänhetens åkning</h3>``
  plus "Tiderna gäller från och med vecka 42 år 2026 till vecka 9 år 2027",
  tables Dag | Tid with weekday rows ("Måndag").

Each rink sub-block has a paragraph stating the default stick rule, "om
ingenting annat anges bland öppettiderna nedan": A-hallen "klubb-, puck- och
bollförbud" (no_stick), B-hallen "tillåtet med klubba, puck och bollar"
(with_stick). Times without their own wording get that default; a time can
override it, e.g. "20.00-22.00 (Inte tillåtet med klubba, puck och bollar)".

A Tid cell holds one ``<p>`` per time. Qualifiers we understand:
"(08.00–09.00 från och med vecka 50)" (slot starts that week),
"till och med vecka N", and "13.15–15.15 - ej 2/11 då installationsarbeten ska
genomföras." (no session on 2/11). Any other text next to a time is warned about
and the time is skipped. "Stängt för allmänheten" = closed.

The ordinary table is expanded over ``recurring_window`` cut at the stated
season. A holiday block that is not entirely past replaces the weekly times for
every date in its range, for each rink it has a table for.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from bs4 import BeautifulSoup, Tag

from .common import (
    STICK_WITHOUT, TIME_RANGE_RE, AdapterResult, BadTimeRange, HallResult, ParseError, Session,
    Slot, dm_date, expand_weekly, http_get, iso_week_monday, iso_week_sunday, month_number,
    norm_space, recurring_window, stick_from_text, sv_date, time_ranges, week_year, weekday_number,
)

NAME = "sigtuna-web"
MUNICIPALITY = "Sigtuna"
SOURCE_URL = "https://www.sigtuna.se/uppleva-och-gora/trana-och-idrotta/skridskoakning-och-ishall/ishall.html"

FACILITY = "Pinbackshallen"
RINKS = {"a-hallen": "A-hallen", "b-hallen": "B-hallen"}

SEASON_RE = re.compile(
    r"från\s+och\s+med\s+vecka\s+(\d{1,2})\s+(?:år\s+)?(\d{4})\s+till(?:\s+och\s+med)?\s+vecka\s+(\d{1,2})\s+(?:år\s+)?(\d{4})",
    re.I)
RANGE_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s*([a-zåäö]+)?\s*(?:[–\-]|till)\s*(\d{1,2})\s+([a-zåäö]+)(?:\s+(\d{4}))?", re.I)
ROW_DATE_RE = re.compile(r"(\d{1,2})\s+([a-zåäö]+)", re.I)
CLOSED_RE = re.compile(r"stängt|stängd|inställ", re.I)
FROM_WEEK_RE = re.compile(r"från\s+och\s+med\s+vecka\s+(\d{1,2})", re.I)
TO_WEEK_RE = re.compile(r"till\s+och\s+med\s+vecka\s+(\d{1,2})", re.I)
EXCEPT_RE = re.compile(
    r"\bej\s+((?:\d{1,2}/\d{1,2})(?:(?:\s*,\s*|\s+och\s+)\d{1,2}/\d{1,2})*)(?:\s+då\b[^()]*)?", re.I)
STICK_PAREN_RE = re.compile(r"\([^)]*\b(?:klubb|puck|boll)[^)]*\)", re.I)
DEFAULT_RE = re.compile(r"om\s+ingenting\s+annat\s+anges", re.I)


def fetch() -> dict[str, str]:
    return {"page": http_get(SOURCE_URL)}


def hall_id(rink: str) -> str:
    return "sigtuna-pinbackshallen-" + rink.lower()


def _default_stick(text: str) -> str:
    if re.search(r"klubb\S*[^.]{0,40}förbud", text, re.I):
        return STICK_WITHOUT
    stick = stick_from_text(text)
    if stick is None:
        raise ParseError(f"Unrecognised default stick rule: {text!r}")
    return stick


def _cell_parts(td: Tag) -> list[str]:
    ps = td.find_all("p")
    texts = [norm_space(p.get_text(" ")) for p in ps] if ps else [norm_space(td.get_text(" "))]
    return [t for t in texts if t]


class _Time:
    """One readable time from a Tid cell."""
    def __init__(self, start, end, stick, valid_from=None, valid_to=None, skip=()):
        self.start, self.end, self.stick = start, end, stick
        self.valid_from, self.valid_to, self.skip = valid_from, valid_to, set(skip)


def _parse_time(part: str, default: str, today: date, where: str, warnings: list[str]) -> _Time | None:
    text = part.strip()
    if text.startswith("(") and text.endswith(")") and text.count("(") == 1:
        text = text[1:-1]
    try:
        ranges = time_ranges(text)
    except BadTimeRange as exc:
        warnings.append(f"{where}: unreadable time {part!r} ({exc})")
        return None
    if len(ranges) != 1:
        warnings.append(f"{where}: unreadable time {part!r}")
        return None
    rest = TIME_RANGE_RE.sub("", text, count=1)
    stick = stick_from_text(rest)
    rest = STICK_PAREN_RE.sub("", rest) if stick else rest
    valid_from = valid_to = None
    skip = []
    m = FROM_WEEK_RE.search(rest)
    if m:
        w = int(m.group(1))
        valid_from = iso_week_monday(week_year(w, today), w)
        rest = rest.replace(m.group(0), "")
    m = TO_WEEK_RE.search(rest)
    if m:
        w = int(m.group(1))
        valid_to = iso_week_sunday(week_year(w, today), w)
        rest = rest.replace(m.group(0), "")
    m = EXCEPT_RE.search(rest)
    if m:
        skip = [dm_date(d, mo, today) for d, mo in re.findall(r"(\d{1,2})/(\d{1,2})", m.group(1))]
        rest = rest.replace(m.group(0), "")
    if re.sub(r"[\s\-–,.;:()]", "", rest):
        warnings.append(f"{where}: time with unrecognised qualifier skipped: {part!r}")
        return None
    s, e = ranges[0]
    return _Time(s, e, stick or default, valid_from, valid_to, skip)


def _block_range(title: str, today: date) -> tuple[date, date] | None:
    m = RANGE_RE.search(title)
    if not m:
        return None
    d1, mo1, d2, mo2, y = m.groups()
    mo1 = mo1 or mo2
    try:
        month_number(mo1), month_number(mo2)
    except ParseError:
        return None
    end = sv_date(d2, mo2, today, y)
    start = sv_date(d1, mo1, today, end.year - (1 if month_number(mo1) > end.month else 0))
    return start, end


class _Hall:
    def __init__(self, rink: str):
        self.result = HallResult(hall_id=hall_id(rink), name=FACILITY, rink=rink)
        self.templates: list[tuple[date, date, list[tuple[int, _Time]]]] = []
        self.dated: dict[date, list[_Time]] = {}


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    soup = BeautifulSoup(raw["page"], "lxml")
    start_h2 = None
    for h2 in soup.find_all("h2"):
        if norm_space(h2.get_text(" ")).lower() == "allmänhetens åkning":
            start_h2 = h2
            break
    if start_h2 is None:
        raise ParseError("No 'Allmänhetens åkning' heading – has the page layout changed?")

    halls = {k: _Hall(v) for k, v in RINKS.items()}
    warnings: list[str] = []
    block: tuple | None = None          # ("ordinary", from, to) | ("dated", start, end) | ("unknown", title)
    rink: str | None = None
    defaults: dict[str, str] = {}
    ordinary_rinks: set[str] = set()
    seasons: list[tuple[date, date]] = []

    for el in start_h2.find_all_next(["h2", "h3", "h4", "p", "table"]):
        if el.name == "h2":
            break
        if el.name == "p" and el.find_parent("table") is not None:
            continue
        text = norm_space(el.get_text(" "))
        if el.name == "h3":
            rink, defaults = None, {}
            if re.search(r"ordinarie", text, re.I):
                block = ["ordinary", None, None]
            else:
                rng = _block_range(text, today)
                block = ["dated", *rng] if rng else ["unknown", text]
            continue
        if el.name == "h4":
            rink = slug if (slug := text.lower().strip()) in RINKS else None
            if rink is None:
                warnings.append(f"Unknown rink heading {text!r}")
            continue
        if el.name == "p":
            if block and block[0] == "ordinary" and (m := SEASON_RE.search(text)):
                w1, y1, w2, y2 = map(int, m.groups())
                block[1], block[2] = iso_week_monday(y1, w1), iso_week_sunday(y2, w2)
                seasons.append((block[1], block[2]))
            if rink and DEFAULT_RE.search(text):
                defaults[rink] = _default_stick(text)
            continue
        # table
        if block is None or block[0] == "unknown":
            raise ParseError(f"Schedule table under an unrecognised heading: {block!r}")
        if rink is None:
            raise ParseError("Schedule table without a known A-/B-hallen heading")
        if rink not in defaults:
            raise ParseError(f"{rink}: no default stick rule before the table")
        hall = halls[rink]
        hid = hall.result.hall_id
        if block[0] == "ordinary":
            if block[1] is None:
                raise ParseError("Ordinary times without a stated season")
            ordinary_rinks.add(rink)
            slots = []
            for day_text, parts in _rows(el):
                wd = weekday_number(day_text.split()[0]) if day_text else None
                if wd is None or len(day_text.split()) != 1:
                    raise ParseError(f"{hid}: unexpected day cell {day_text!r} in ordinary table")
                for p in parts:
                    if CLOSED_RE.search(p):
                        continue
                    t = _parse_time(p, defaults[rink], today, f"{hid} {day_text}", warnings)
                    if t:
                        slots.append((wd, t))
            hall.templates.append((block[1], block[2], slots))
            continue
        _, b_start, b_end = block
        if b_end < today:
            continue  # holiday block entirely in the past
        d = b_start
        while d <= b_end:
            hall.dated.setdefault(d, [])
            d += timedelta(days=1)
        for day_text, parts in _rows(el):
            m = ROW_DATE_RE.search(day_text)
            wd = weekday_number(day_text.split()[0]) if day_text else None
            if not m or wd is None:
                warnings.append(f"{hid}: unreadable day {day_text!r} in {b_start}..{b_end}")
                continue
            mon = month_number(m.group(2))
            day = sv_date(m.group(1), m.group(2), today, b_end.year - (1 if mon > b_end.month else 0))
            if day.weekday() != wd or not (b_start <= day <= b_end):
                warnings.append(f"{hid}: day {day_text!r} does not fit {b_start}..{b_end}")
                continue
            for p in parts:
                if CLOSED_RE.search(p):
                    continue
                t = _parse_time(p, defaults[rink], today, f"{hid} {day_text}", warnings)
                if t and day not in t.skip:
                    hall.dated[day].append(t)

    missing = set(RINKS) - ordinary_rinks
    if missing:
        raise ParseError(f"No ordinary table for {sorted(missing)} – has the page layout changed?")

    window = recurring_window(today, min(s for s, _ in seasons), max(e for _, e in seasons))
    empty_reason = None
    if window is None:
        window = recurring_window(today)
        empty_reason = "The ordinary season stated on the page is over or has not started"
    lo, hi = window

    for hall in halls.values():
        sessions: list[Session] = []
        if empty_reason is None:
            for s_from, s_to, slots in hall.templates:
                for wd, t in slots:
                    a = max(lo, s_from, t.valid_from or lo)
                    b = min(hi, s_to, t.valid_to or hi)
                    if a <= b:
                        sessions += expand_weekly([Slot(wd, t.start, t.end, t.stick)], a, b, SOURCE_URL,
                                                  skip=set(hall.dated) | t.skip)
        for d, ts in hall.dated.items():
            if lo <= d <= hi:
                sessions += [Session(d.isoformat(), t.start, t.end, t.stick, SOURCE_URL) for t in ts]
        seen = set()
        for s in sorted(sessions, key=lambda s: (s.date, s.start, s.end)):
            key = (s.date, s.start, s.end, s.stick)
            if key not in seen:
                seen.add(key)
                hall.result.sessions.append(s)

    return AdapterResult(
        covers_from=lo.isoformat(), covers_to=hi.isoformat(), source_updated=None,
        halls={h.result.hall_id: h.result for h in halls.values()},
        warnings=warnings, empty_reason=empty_reason,
    )


def _rows(table: Tag) -> list[tuple[str, list[str]]]:
    out = []
    body = table.find("tbody") or table
    for tr in body.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        if len(tds) != 2:
            raise ParseError(f"Expected Dag|Tid rows, got {len(tds)} cells")
        out.append((norm_space(tds[0].get_text(" ")), _cell_parts(tds[1])))
    return out
