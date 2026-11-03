"""Nynäshamns kommun: nynashamn.se "Ishallen, HLL Arena"

Page layout (as of Nov 2026), Sitevision text portlets (no <main> element):

    <h2>Säsong mellan augusti-april</h2>
    <p>... Allmänhetens åkning startar 11 september v 37.</p>
    <h2>Tider för allmänhetens åkning säsongen 2026/2027</h2>
    <ul><li><strong>Fredagar:</strong> 15.00-16.45 (15.00-16.00 <strong>utan</strong> puck och
        klubba, 16.00 – 16.45 <strong>med</strong> puck och klubba).</li> ...</ul>
    <h2>Observera - ändrade tider på grund av händelser i ishallen</h2>
    <ul><li><strong>Lördagen 12 december</strong> är allmänhetens åkning inställd ...</li>
        <li><strong>Söndagen 13 december</strong> förlängs allmänhetens åkning. Ny tid 8.30-12.00
            (8.30-10.30 – utan klubba och puck, 10.30-12.30 – med puck och klubba)</li> ...</ul>
    <p>Sidan senast ändrad: <time datetime="2026-10-30T16:00:00+01:00">2026-10-30</time></p>

One hall, HLL Arena. Each weekly line has an overall range and, in parentheses, its
split into a no-stick and a stick part; we emit the parts (with their own stick wording),
not the overall range. The parts must exactly tile the overall range, otherwise the line
is not trusted: warning, no sessions (e.g. the 13 December change above says 8.30-12.00
but its parts end at 12.30).

The weekly template is expanded over `recurring_window(today, start, end)`: start is the
"startar 11 september" date; end is the last day of the end month in the "Säsong mellan
augusti-april" heading (30 April of the season's second year), since the page states no
exact last date. Dates under "Observera - ändrade tider" replace the template: "inställd"
-> no skating that day, a new time -> those sessions. Change entries outside the window
are ignored. Years come from "säsongen 2026/2027" (July-December = first year).
"""
from __future__ import annotations

import calendar
import re
from datetime import date

from bs4 import BeautifulSoup, Tag

from .common import (
    STICK_UNKNOWN, TIME_RANGE_RE,
    AdapterResult, BadTimeRange, HallResult, ParseError, Session, Slot,
    expand_weekly, http_get, month_number, norm_space, recurring_window,
    stick_from_text, time_ranges, weekday_number,
)

NAME = "nynashamn-web"
MUNICIPALITY = "Nynäshamn"
SOURCE_URL = "https://nynashamn.se/service/kultur--fritid/fritid/ishallen-hll-arena"

HALL_NAME = "HLL Arena"
HALL_ID = "nynashamn-hll-arena"

TIMES_HEADING_RE = re.compile(r"tider\s+för\s+allmänhetens\s+åkning\s+säsongen\s+(\d{4})\s*/\s*(\d{4})", re.I)
CHANGES_HEADING_RE = re.compile(r"ändrade\s+tider", re.I)
SEASON_SPAN_RE = re.compile(r"säsong\s+mellan\s+([a-zåäö]+)\s*[-–]\s*([a-zåäö]+)", re.I)
START_RE = re.compile(r"allmänhetens\s+åkning\s+startar\s+(\d{1,2})\s+([a-zåäö]+)", re.I)
DATE_RE = re.compile(r"(?:([a-zåäö]+)\s+(?:den\s+)?)?(\d{1,2})\s+(januari|februari|mars|april|maj|juni|juli|"
                     r"augusti|september|oktober|november|december)\b", re.I)
CANCEL_RE = re.compile(r"inställd|stängd|ingen\s+allmänhetens", re.I)
UPDATED_RE = re.compile(r"senast\s+ändrad", re.I)


def fetch() -> dict[str, str]:
    return {"page": http_get(SOURCE_URL)}


def _text(el: Tag) -> str:
    return norm_space(el.get_text(" "))


def split_sessions(text: str) -> list[tuple[str, str, str, str | None]]:
    """'15.00-16.45 (15.00-16.00 utan puck och klubba, 16.00 – 16.45 med puck och klubba)'
    -> [(start, end, stick, note), ...]. Raises ValueError when the line is not consistent."""
    m = re.match(r"^(.*?)\((.*)\)\s*\.?\s*$", text)
    head, inner = (m.group(1), m.group(2)) if m else (text, None)
    overall = time_ranges(head)
    if len(overall) != 1:
        raise ValueError(f"expected one time range before the parenthesis in {text!r}")
    if inner is None:
        wording = norm_space(TIME_RANGE_RE.sub("", head)).strip(" .,–-")
        stick = stick_from_text(wording)
        return [(overall[0][0], overall[0][1], stick or STICK_UNKNOWN, None if stick else (wording or None))]
    matches = list(TIME_RANGE_RE.finditer(inner))
    if not matches:
        raise ValueError(f"no times inside the parenthesis in {text!r}")
    parts = []
    for i, tm in enumerate(matches):
        (s, e), = time_ranges(tm.group(0))
        tail = inner[tm.end(): matches[i + 1].start() if i + 1 < len(matches) else len(inner)]
        wording = norm_space(tail).strip(" .,;–-")
        stick = stick_from_text(wording)
        parts.append((s, e, stick or STICK_UNKNOWN, None if stick else (wording or None)))
    if parts[0][0] != overall[0][0] or parts[-1][1] != overall[0][1] or any(
            a[1] != b[0] for a, b in zip(parts, parts[1:])):
        raise ValueError(f"parts do not add up to {overall[0][0]}-{overall[0][1]} in {text!r}")
    return parts


def _season_date(day: str | int, month_word: str, years: tuple[int, int]) -> date:
    mon = month_number(month_word)
    y = years[0] if mon >= 7 else years[1]
    try:
        return date(y, mon, int(day))
    except ValueError:
        raise ParseError(f"Invalid date {day} {month_word}") from None


def _following_lists(h2: Tag) -> list[Tag]:
    out = []
    for sib in h2.find_next_siblings():
        if sib.name in ("h1", "h2"):
            break
        if sib.name in ("ul", "ol"):
            out.append(sib)
    return out


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    soup = BeautifulSoup(raw["page"], "lxml")
    warnings: list[str] = []

    times_h2 = None
    years: tuple[int, int] | None = None
    changes_h2 = None
    season_end: date | None = None
    for h2 in soup.find_all("h2"):
        t = _text(h2)
        m = TIMES_HEADING_RE.search(t)
        if m:
            if times_h2 is not None:
                raise ParseError("Two 'Tider för allmänhetens åkning' sections")
            times_h2, years = h2, (int(m.group(1)), int(m.group(2)))
        elif CHANGES_HEADING_RE.search(t):
            changes_h2 = h2
    if times_h2 is None:
        raise ParseError("No 'Tider för allmänhetens åkning säsongen YYYY/YYYY' heading - layout changed?")

    page = norm_space(soup.get_text(" "))
    sm = START_RE.search(page)
    season_start = _season_date(sm.group(1), sm.group(2), years) if sm else None
    em = SEASON_SPAN_RE.search(page)
    if em:
        end_mon = month_number(em.group(2))
        end_year = years[1] if end_mon < 7 else years[0]
        season_end = date(end_year, end_mon, calendar.monthrange(end_year, end_mon)[1])

    # weekly template
    slots: list[Slot] = []
    lists = _following_lists(times_h2)
    if not lists:
        raise ParseError("No list under the times heading")
    for li in (li for ul in lists for li in ul.find_all("li", recursive=False)):
        line = _text(li)
        day_word, sep, rest = line.partition(":")
        wd = weekday_number(day_word) if sep else None
        if wd is None:
            if _has_time(line):
                warnings.append(f"{HALL_ID}: unexpected line {line!r}")
            continue  # e.g. the "Åk med eller utan klubba (förtydligande)" list
        try:
            parts = split_sessions(rest.strip())
        except (ValueError, BadTimeRange) as exc:
            warnings.append(f"{HALL_ID}: could not read {line!r} ({exc})")
            continue
        slots += [Slot(weekday=wd, start=s, end=e, stick=st, note=n) for s, e, st, n in parts]
    if not slots and not warnings:
        raise ParseError("Times list has no times")

    window = recurring_window(today, season_start, season_end)
    hall = HallResult(hall_id=HALL_ID, name=HALL_NAME, rink=None)
    updated = _updated(soup)
    if window is None:
        win = recurring_window(today)
        return AdapterResult(
            covers_from=win[0].isoformat(), covers_to=win[1].isoformat(),
            source_updated=updated, halls={HALL_ID: hall}, warnings=warnings,
            empty_reason="Outside the public-skating season stated on the page",
        )

    # changed / cancelled dates
    overrides: dict[date, list[Session]] = {}
    if changes_h2 is not None:
        for li in (li for ul in _following_lists(changes_h2) for li in ul.find_all("li", recursive=False)):
            line = _text(li)
            dates, mismatch = [], False
            for dm in DATE_RE.finditer(line):
                wd_word, day, mon = dm.groups()
                d = _season_date(day, mon, years)
                wd = weekday_number(wd_word) if wd_word else None
                if wd is not None and wd != d.weekday() and window[0] <= d <= window[1]:
                    mismatch = True
                dates.append(d)
            if not dates:
                warnings.append(f"{HALL_ID}: change without a date {line!r}")
                continue
            in_window = [d for d in dates if window[0] <= d <= window[1]]
            if not in_window:
                continue  # outside what we cover
            if mismatch:
                warnings.append(f"{HALL_ID}: weekday does not match date in {line!r}")
            if CANCEL_RE.search(line):
                for d in in_window:
                    overrides[d] = []
                continue
            new = re.split(r"ny\s+tid", line, maxsplit=1, flags=re.I)
            try:
                if len(new) != 2:
                    raise ValueError("no 'Ny tid'")
                parts = split_sessions(new[1].strip(" :"))
            except (ValueError, BadTimeRange) as exc:
                warnings.append(f"{HALL_ID}: could not read change {line!r} ({exc})")
                for d in in_window:
                    overrides[d] = []  # the weekly time no longer applies either
                continue
            for d in in_window:
                overrides[d] = [Session(date=d.isoformat(), start=s, end=e, stick=st,
                                        source_url=SOURCE_URL, note=n, changed=True)
                                for s, e, st, n in parts]

    sessions = expand_weekly(slots, window[0], window[1], SOURCE_URL, skip=set(overrides))
    for ss in overrides.values():
        sessions += ss
    hall.sessions = _dedupe(sessions)
    return AdapterResult(
        covers_from=window[0].isoformat(), covers_to=window[1].isoformat(),
        source_updated=updated, halls={HALL_ID: hall}, warnings=warnings,
    )


def _has_time(line: str) -> bool:
    try:
        return bool(time_ranges(line))
    except BadTimeRange:
        return True


def _updated(soup: BeautifulSoup) -> str | None:
    for t in soup.find_all("time"):
        parent = t.find_parent(["p", "div", "span"])
        if parent is None or not UPDATED_RE.search(parent.get_text(" ")):
            continue
        m = re.match(r"(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2}))?", t.get("datetime", "") or _text(t))
        if m:
            return f"{m.group(1)}T{m.group(2) or '00:00'}"
    return None


def _dedupe(sessions: list[Session]) -> list[Session]:
    seen, out = set(), []
    for s in sorted(sessions, key=lambda x: (x.date, x.start, x.end, x.stick)):
        k = (s.date, s.start, s.end, s.stick)
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out
