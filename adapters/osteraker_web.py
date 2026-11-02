"""Österåkers kommun: osteraker.se "Ishallar" (Österåkers sportcentrum, Ishall 1 and 2)

Page layout (as of Nov 2026), Sitevision text portlets inside <main>:

    <p>Förskolan och allmänheten har tider igen i augusti 2026. ...</p>     (stale, ignored)
    <h2>Förskolans tider</h2>                                               (preschool: skipped)
      <h3>Ishall 1, 17 augusti 2026 – 6 april 2027</h3><table>...</table>
    <h2>Allmänhetens åkning</h2>
      <h3>Ishall 1, 17 augusti 2026 – 11 april 2027</h3>
      <table> Dag | Tid | Övrig information
              måndagar–fredagar | 14–15.20 | Utan klubbor
              lördagar och söndagar | 13.30–14.50 | Utan klubbor </table>
    <h2>Allmänhetens åkning</h2>
      <h3>Ishall 2, 17 augusti 2026 – 11 april 2027</h3><table>...</table>
    <h2>Höstlov 26 oktober – 1 november</h2>
      <h3>Ishall 1 allmänhetens åkning under höstlovet</h3>
      <table> 26-30 oktober | 8.00 – 13.15 | Utan klubbor
              lördag och söndag | 13.30-14.50 | Utan klubbor </table>
      <h3>Ishall 2 allmänhetens åkning under höstlovet</h3>
      <table> ... 29-30 oktober (avbrott för spoltid) | 12-14, 14.10-15.50 | Med klubbor ... </table>
    ... "Senast uppdaterad: <time datetime="2026-10-26">26 oktober 2026</time>"

"Allmänhetens åkning" tables are weekly templates valid between the dates in their h3;
expanded over `recurring_window`. Outside those dates the halls are closed (the page says
the halls are open from late August to March/April).

A "<name>lov <date> – <date>" h2 starts a holiday block: its per-hall tables replace that
hall's weekly template on every date of the block (a date the table does not list has no
public skating). Row days are a date range ("26-30 oktober"), a single date, or weekdays
("lördag och söndag") meaning those weekdays within the block. Blocks entirely in the past
are skipped unparsed. A row whose time cell says "stängt"/"inställt" means no skating. A future block that adjoins the recurring window extends `covers`.
`covers` is shared by both halls, so it only extends as far as every hall is described.

Stick wording is in the "Övrig information" column ("Med/Utan klubbor"); no default.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from bs4 import BeautifulSoup, Tag

from .common import (
    STICK_UNKNOWN,
    AdapterResult, BadTimeRange, HallResult, ParseError, Session, Slot,
    expand_weekly, http_get, month_number, norm_space, recurring_window, slugify,
    stick_from_text, sv_date, time_ranges, weekday_number,
)

NAME = "osteraker-web"
MUNICIPALITY = "Österåker"
SOURCE_URL = ("https://www.osteraker.se/upplevagora/idrottmotionochfriluftsliv/"
              "hallarochidrottsanlaggningar/ishallar.4.5f4ce35f18ecbde71808d58.html")

FACILITY = "Österåkers sportcentrum"
RINKS = ["Ishall 1", "Ishall 2"]

_MONTHS = r"(?:januari|februari|mars|april|maj|juni|juli|augusti|september|oktober|november|december)"
_DATE = r"(\d{1,2})\s+(" + _MONTHS + r")(?:\s+(\d{4}))?"
RANGE_RE = re.compile(_DATE + r"\s*[–—-]\s*" + _DATE, re.I)
SHORT_RANGE_RE = re.compile(r"^(\d{1,2})\s*[–—-]\s*(\d{1,2})\s+(" + _MONTHS + r")$", re.I)
SINGLE_RE = re.compile(r"^(\d{1,2})\s+(" + _MONTHS + r")$", re.I)
SEASON_H3_RE = re.compile(r"^(ishall\s+\d+)\s*,\s*(.+)$", re.I)
RINK_RE = re.compile(r"^(ishall\s+\d+)\b", re.I)
HOLIDAY_H2_RE = re.compile(r"^\w*lov\w*\b", re.I)
AGAIN_RE = re.compile(r"har\s+tider\s+igen\s+i\s+" + _MONTHS + r"\s+\d{4}", re.I)
DIGIT_TIME_RE = re.compile(r"\d")
CLOSED_RE = re.compile(r"(stängt|inställt|ingen\s+åkning)\.?", re.I)


def fetch() -> dict[str, str]:
    return {"page": http_get(SOURCE_URL)}


def hall_id(rink: str) -> str:
    return "osteraker-" + slugify(FACILITY) + "-" + slugify(rink)


def _text(el: Tag) -> str:
    return norm_space(el.get_text(" "))


def _rink(text: str) -> str | None:
    m = RINK_RE.match(text)
    if not m:
        return None
    name = "Ishall " + m.group(1).split()[-1]
    return name if name in RINKS else None


def _date_range(text: str, today: date) -> tuple[date, date] | None:
    m = RANGE_RE.search(text)
    if not m:
        return None
    d1, m1, y1, d2, m2, y2 = m.groups()
    end = sv_date(d2, m2, today, y2)
    if y1:
        start = sv_date(d1, m1, today, y1)
    else:
        start = sv_date(d1, m1, today, end.year)
        if start > end:
            start = sv_date(d1, m1, today, end.year - 1)
    if end < start:
        raise ParseError(f"Date range ends before it starts: {text!r}")
    return start, end


def weekdays_spec(text: str) -> list[int] | None:
    """'måndagar–fredagar' -> [0..4], 'lördag och söndag' -> [5, 6]; None if not weekdays."""
    out: list[int] = []
    for tok in re.split(r",|\boch\b", text.lower()):
        tok = tok.strip()
        if not tok:
            continue
        parts = [p.strip() for p in re.split(r"[–—-]", tok)]
        if len(parts) == 1:
            wd = weekday_number(parts[0])
            if wd is None:
                return None
            out.append(wd)
        elif len(parts) == 2:
            a, b = weekday_number(parts[0]), weekday_number(parts[1])
            if a is None or b is None or b < a:
                return None
            out += list(range(a, b + 1))
        else:
            return None
    return sorted(set(out)) or None


def _block_dates(spec: str, b_from: date, b_to: date) -> list[date] | None:
    """Dates a holiday-table row applies to, or None if the day cell is unreadable."""
    spec = norm_space(re.sub(r"\(.*?\)", "", spec))

    def mk(day: str, mon: str) -> date:
        m = month_number(mon)
        y = b_from.year if (m, int(day)) >= (b_from.month, b_from.day) else b_to.year
        return date(y, m, int(day))

    try:
        m = SHORT_RANGE_RE.match(spec)
        if m:
            a, b = mk(m.group(1), m.group(3)), mk(m.group(2), m.group(3))
        elif SINGLE_RE.match(spec):
            m = SINGLE_RE.match(spec)
            a = b = mk(m.group(1), m.group(2))
        elif RANGE_RE.fullmatch(spec):
            m = RANGE_RE.fullmatch(spec)
            a, b = mk(m.group(1), m.group(2)), mk(m.group(4), m.group(5))
        else:
            wds = weekdays_spec(spec)
            if wds is None:
                return None
            out, d = [], b_from
            while d <= b_to:
                if d.weekday() in wds:
                    out.append(d)
                d += timedelta(days=1)
            return out
    except (ValueError, ParseError):
        return None
    if b < a or a < b_from or b > b_to:
        return None
    return [a + timedelta(days=i) for i in range((b - a).days + 1)]


def _rows(table: Tag) -> list[list[str]]:
    rows = []
    for tr in table.find_all("tr"):
        if tr.find_parent("thead") or tr.find("th"):
            continue
        rows.append([_text(c) for c in tr.find_all("td")])
    return rows


def _stick(text: str) -> tuple[str, str | None]:
    s = stick_from_text(text)
    return (s, None) if s else (STICK_UNKNOWN, text or None)


def _times(hid: str, cells: list[str], warnings: list[str]) -> list[tuple[str, str]] | None:
    try:
        ranges = time_ranges(cells[1])
    except BadTimeRange as exc:
        warnings.append(f"{hid}: could not read {cells!r} ({exc})")
        return None
    if not ranges:
        warnings.append(f"{hid}: could not read {cells!r}")
        return None
    return ranges


def _merge_covers(ranges: list[tuple[date, date]]) -> tuple[date, date] | None:
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

    templates: dict[str, tuple[list[Slot], date, date]] = {}
    blocks: dict[str, list[tuple[date, date, dict[date, list[Session]]]]] = {}

    mode: str | None = None
    block: tuple[date, date] | None = None
    rink: str | None = None
    season: tuple[date, date] | None = None
    for el in root.find_all(["h2", "h3", "table"]):
        if el.name == "h2":
            t = _text(el)
            rink, season, block = None, None, None
            if re.fullmatch(r"allmänhetens\s+åkning", t, re.I):
                mode = "recurring"
            elif re.match(r"förskolan", t, re.I):
                mode = "skip"
            elif HOLIDAY_H2_RE.match(t):
                block = _date_range(t, today)
                if block is None:
                    raise ParseError(f"Holiday heading without dates: {t!r}")
                mode = "past" if block[1] < today else "holiday"
            else:
                mode = "other"
            continue
        if el.name == "h3":
            t = _text(el)
            if mode == "recurring":
                m = SEASON_H3_RE.match(t)
                rink = _rink(t)
                season = _date_range(m.group(2), today) if m else None
                if rink is None or season is None:
                    raise ParseError(f"Unexpected heading under 'Allmänhetens åkning': {t!r}")
            elif mode == "holiday":
                rink = _rink(t)
                if rink is None:
                    raise ParseError(f"Unexpected heading in holiday block: {t!r}")
            continue
        # table
        if mode in ("skip", "past"):
            continue
        if mode in (None, "other") or rink is None:
            if DIGIT_TIME_RE.search(_text(el)) and _has_time(_text(el)):
                warnings.append(f"unexpected table with times: {_text(el)[:80]!r}")
            continue
        hid = hall_id(rink)
        if mode == "recurring":
            if rink in templates:
                raise ParseError(f"Two weekly tables for {rink}")
            slots: list[Slot] = []
            for cells in _rows(el):
                if len(cells) != 3:
                    raise ParseError(f"{hid}: unexpected row {cells!r}")
                wds = weekdays_spec(cells[0])
                if wds is None:
                    warnings.append(f"{hid}: unknown days in {cells!r}")
                    continue
                ranges = _times(hid, cells, warnings)
                if ranges is None:
                    continue
                stick, note = _stick(cells[2])
                slots += [Slot(weekday=wd, start=s, end=e, stick=stick, note=note)
                          for wd in wds for s, e in ranges]
            templates[rink] = (slots, season[0], season[1])
        else:  # holiday
            b_from, b_to = block
            days: dict[date, list[Session]] = {}
            for cells in _rows(el):
                if len(cells) != 3:
                    raise ParseError(f"{hid}: unexpected row {cells!r}")
                dates = _block_dates(cells[0], b_from, b_to)
                if dates is None:
                    warnings.append(f"{hid}: unknown days in {cells!r}")
                    continue
                if CLOSED_RE.fullmatch(cells[1]) and not _has_time(" ".join(cells)):
                    for d in dates:
                        days.setdefault(d, [])
                    continue
                ranges = _times(hid, cells, warnings)
                if ranges is None:
                    continue
                stick, note = _stick(cells[2])
                for d in dates:
                    days.setdefault(d, []).extend(
                        Session(date=d.isoformat(), start=s, end=e, stick=stick,
                                source_url=SOURCE_URL, note=note) for s, e in ranges)
            blocks.setdefault(rink, []).append((b_from, b_to, days))

    updated = _updated(soup)
    if not templates and AGAIN_RE.search(_text(root)) and not blocks:
        win = recurring_window(today)
        return AdapterResult(
            covers_from=win[0].isoformat(), covers_to=win[1].isoformat(), source_updated=updated,
            halls={hall_id(r): HallResult(hall_id=hall_id(r), name=FACILITY, rink=r) for r in RINKS},
            warnings=warnings, empty_reason="Off season: page says public times resume later",
        )
    missing = [r for r in RINKS if r not in templates]
    if missing:
        raise ParseError(f"No public skating table for {missing} - has the page layout changed?")

    hall_covers: dict[str, tuple[date, date] | None] = {}
    for r, (_slots, s_from, s_to) in templates.items():
        ranges = []
        win = recurring_window(today, s_from, s_to)
        if win:
            ranges.append(win)
        ranges += [(max(b0, today), b1) for b0, b1, _d in blocks.get(r, [])]
        hall_covers[r] = _merge_covers(ranges)

    if all(c is None for c in hall_covers.values()):
        win = recurring_window(today)
        return AdapterResult(
            covers_from=win[0].isoformat(), covers_to=win[1].isoformat(), source_updated=updated,
            halls={hall_id(r): HallResult(hall_id=hall_id(r), name=FACILITY, rink=r) for r in RINKS},
            warnings=warnings, empty_reason="Outside the public-skating season stated on the page",
        )
    known = [c for c in hall_covers.values() if c]
    lo, hi = min(c[0] for c in known), min(c[1] for c in known)

    halls: dict[str, HallResult] = {}
    for r in RINKS:
        slots, s_from, s_to = templates[r]
        overridden: dict[date, list[Session]] = {}
        for b_from, b_to, days in blocks.get(r, []):
            d = b_from
            while d <= b_to:
                overridden[d] = days.get(d, [])
                d += timedelta(days=1)
        sessions: list[Session] = []
        win = recurring_window(today, s_from, s_to)
        if win:
            sessions += expand_weekly(slots, win[0], win[1], SOURCE_URL, skip=set(overridden))
        for d, ss in overridden.items():
            sessions += ss
        hr = HallResult(hall_id=hall_id(r), name=FACILITY, rink=r)
        hr.sessions = _dedupe([s for s in sessions if lo.isoformat() <= s.date <= hi.isoformat()])
        halls[hr.hall_id] = hr

    return AdapterResult(
        covers_from=lo.isoformat(), covers_to=hi.isoformat(), source_updated=updated,
        halls=halls, warnings=warnings,
    )


def _has_time(text: str) -> bool:
    try:
        return bool(time_ranges(text))
    except BadTimeRange:
        return True


def _updated(soup: BeautifulSoup) -> str | None:
    for t in soup.find_all("time"):
        parent = t.find_parent(["span", "p", "div"])
        if parent is None or not re.search(r"senast\s+uppdaterad", parent.get_text(" "), re.I):
            continue
        m = re.match(r"(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2}))?", t.get("datetime", ""))
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
