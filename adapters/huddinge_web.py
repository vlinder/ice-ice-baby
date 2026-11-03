"""Huddinge kommun: huddinge.se "Skridsko- och skidåkning".

Page layout (as of Nov 2026, Sitevision): one collapsible section per facility,
``<div class="sv-collapsible-content">`` with an ``<h3>`` title such as
"Björkängshallen B, Stuvsta" and a body ``<div id=...>`` (the h3 link's
``aria-controls``). A body holds, in order:

* optionally an info box ``<h3>Avvikande öppettider</h3><ul><li>10 november:
  Hallen är stängd.</li>…</ul>`` (dated exceptions for the whole section);
* holiday tables, preceded by "Öppet vecka 44 2026 (höstlov)" and captioned /
  headed "Höstlov vecka 44, 26 oktober-1 november 2026";
* the ordinary weekly table, preceded by "Öppet från vecka 34 2026 till vecka 12
  2027." and captioned "Ordinarie tider till och med vecka 12 2027".

Every table has the columns Dag | Tid | Klubba. A cell can hold several ``<p>``
(Stortorpshallen on Wednesdays has two times with one stick value each);
"Stängt" means closed that day. The Klubba column says "Med puck/boll" or
"Utan puck/boll" (mapped with ``stick_from_text``); there is no page-wide default.

"Visättrahallen och Visättra bandyplan" holds two rinks: sub-headings
``<h3>Visättrahallen</h3>`` / ``<h3>Visättra bandyplan</h3>`` say which tables
belong to which. The bandy rink currently only says "Stängd för säsongen", which
we emit as a hall with no sessions.

Rules: the weekly table is expanded over ``recurring_window`` cut at its stated
season; a holiday table replaces the weekly times on the dates of its range
(entirely past holiday tables are skipped unread); "Avvikande öppettider" lines
with "stängd"/"inställd" remove that date, lines with times replace it, other
in-window lines are warned about and leave the weekly times in place.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from bs4 import BeautifulSoup, Tag

from .common import (
    RECURRING_HORIZON_DAYS, STICK_UNKNOWN, TIME_RANGE_RE, AdapterResult, BadTimeRange, HallResult, ParseError,
    Session, Slot, expand_weekly, http_get, iso_week_monday, iso_week_sunday, month_number,
    norm_space, recurring_window, slugify, stick_from_text, sv_date, time_ranges, weekday_number,
)

NAME = "huddinge-web"
MUNICIPALITY = "Huddinge"
SOURCE_URL = "https://www.huddinge.se/fritid-natur-och-kultur/motion-och-idrott/allmanhetensakning/"

# (section title prefix, [(hall name, rink, sub-heading inside the section or None)])
SECTIONS = [
    ("Björkängshallen B", [("Björkängshallen", "B", None)]),
    ("Segeltorpshallen", [("Segeltorpshallen", None, None)]),
    ("Stortorpshallen", [("Stortorpshallen", None, None)]),
    ("Visättrahallen och Visättra bandyplan", [
        ("Visättrahallen", None, "Visättrahallen"),
        ("Visättra bandyplan", None, "Visättra bandyplan"),
    ]),
]

SEASON_RE = re.compile(r"från\s+vecka\s+(\d{1,2})\s+(\d{4})\s+till(?:\s+och\s+med)?\s+vecka\s+(\d{1,2})\s+(\d{4})", re.I)
SEASON_END_RE = re.compile(r"till\s+och\s+med\s+vecka\s+(\d{1,2})\s+(\d{4})", re.I)
RANGE_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s*([a-zåäö]+)?\s*[–\-]\s*(\d{1,2})\s+([a-zåäö]+)(?:\s+(\d{4}))?", re.I)
EXC_RE = re.compile(
    r"^(\d{1,2})(?:\s*[–\-]\s*(\d{1,2}))?\s+([a-zåäö]+)(?:\s+(\d{4}))?\s*:\s*(.*)$", re.I)
CLOSED_RE = re.compile(r"stängd|stängt|inställ", re.I)
SEASON_CLOSED_RE = re.compile(r"stängd\s+för\s+säsongen", re.I)
UPDATED_RE = re.compile(r"Uppdaterad:?\s*(\d{1,2})\s+([a-zåäö]+)\s+(\d{4})", re.I)


def fetch() -> dict[str, str]:
    return {"page": http_get(SOURCE_URL)}


def hall_id(name: str, rink: str | None) -> str:
    return "huddinge-" + slugify(name) + (("-" + slugify(rink)) if rink else "")


def _cell_parts(td: Tag) -> list[str]:
    ps = td.find_all("p")
    texts = [norm_space(p.get_text(" ")) for p in ps] if ps else [norm_space(td.get_text(" "))]
    return [t for t in texts if t]


def _context(table: Tag) -> str:
    """Caption plus the paragraphs before the table in the same text portlet."""
    bits = []
    cap = table.find("caption")
    if cap:
        bits.append(cap.get_text(" "))
    for sib in table.find_previous_siblings():
        if sib.name == "table":
            break
        bits.append(sib.get_text(" "))
    return norm_space(" ".join(bits))


def _parse_range(text: str, today: date) -> tuple[date, date] | None:
    m = RANGE_RE.search(text)
    if not m:
        return None
    d1, mo1, d2, mo2, y = m.groups()
    try:
        month_number(mo2)
        mo1 = mo1 or mo2
        month_number(mo1)
    except ParseError:
        return None
    end = sv_date(d2, mo2, today, y)
    start_year = end.year - (1 if month_number(mo1) > end.month else 0)
    start = sv_date(d1, mo1, today, start_year)
    if end < start:
        raise ParseError(f"Bad date range in {text!r}")
    return start, end


def _parse_rows(table: Tag, hid: str, warnings: list[str]) -> list[tuple[int, str, str, str, str | None]]:
    """Rows -> (weekday, start, end, stick, note). Unreadable cells become warnings."""
    out = []
    body = table.find("tbody") or table
    for tr in body.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        if len(tds) != 3:
            raise ParseError(f"{hid}: expected Dag|Tid|Klubba, got {len(tds)} cells")
        day_text = norm_space(tds[0].get_text(" "))
        wd = weekday_number(day_text)
        times, sticks = _cell_parts(tds[1]), _cell_parts(tds[2])
        if wd is None:
            if any(re.search(r"\d", t) for t in times):
                warnings.append(f"{hid}: unknown day {day_text!r} with times {times!r}")
            continue
        for i, t in enumerate(times):
            if CLOSED_RE.search(t):
                continue
            try:
                ranges = time_ranges(t)
            except BadTimeRange as exc:
                warnings.append(f"{hid}: {day_text}: unreadable time {t!r} ({exc})")
                continue
            leftover = TIME_RANGE_RE.sub("", t).strip(" ,.;")
            if len(ranges) != 1 or leftover:
                warnings.append(f"{hid}: {day_text}: unreadable time {t!r}")
                continue
            if len(sticks) == len(times):
                stick_text = sticks[i]
            elif len(sticks) == 1:
                stick_text = sticks[0]
            else:
                warnings.append(f"{hid}: {day_text}: cannot pair times {times!r} with {sticks!r}")
                continue
            stick = stick_from_text(stick_text)
            s, e = ranges[0]
            out.append((wd, s, e, stick or STICK_UNKNOWN, None if stick else stick_text))
    return out


def _sub_hall_for(table: Tag, subs: list[str]) -> str | None:
    for h in table.find_all_previous("h3"):
        t = norm_space(h.get_text(" "))
        if t in subs:
            return t
        if h.find("a", attrs={"data-env-collapse": True}) is not None:
            return None  # reached the section title
    return None


def _season_closed(h3: Tag) -> bool:
    texts = []
    for el in h3.next_elements:
        if isinstance(el, Tag) and el.name in ("h3", "table"):
            break
        if isinstance(el, str):
            texts.append(el)
    return bool(SEASON_CLOSED_RE.search(" ".join(texts)))


class _Hall:
    def __init__(self, hid: str, name: str, rink: str | None):
        self.result = HallResult(hall_id=hid, name=name, rink=rink)
        self.templates: list[tuple[date | None, date, list[Slot]]] = []
        self.dated: dict[date, list[Session]] = {}   # holiday tables / exception times
        self.closed: set[date] = set()
        self.has_schedule = False


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    soup = BeautifulSoup(raw["page"], "lxml")
    sections: dict[str, Tag] = {}
    warnings: list[str] = []
    for sec in soup.select("div.sv-collapsible-content"):
        h3 = sec.find("h3")
        a = h3.find("a", attrs={"aria-controls": True}) if h3 else None
        body = soup.find(id=a["aria-controls"]) if a else None
        if not a or body is None:
            continue
        title = norm_space(h3.get_text(" "))
        for prefix, _ in SECTIONS:
            if title.startswith(prefix):
                sections[prefix] = body
                break
        else:
            if body.find("table"):
                warnings.append(f"Unknown section {title!r} with a schedule table; not parsed")

    halls: dict[str, _Hall] = {}
    for prefix, members in SECTIONS:
        body = sections.get(prefix)
        if body is None:
            raise ParseError(f"Section {prefix!r} not found – has the page layout changed?")
        sec_halls = []
        for name, rink, _sub in members:
            h = _Hall(hall_id(name, rink), name, rink)
            halls[h.result.hall_id] = h
            sec_halls.append(h)
        subs = [m[2] for m in members if m[2]]
        by_sub = {m[2]: h for m, h in zip(members, sec_halls)}

        # Sub-halls that say "Stängd för säsongen"
        for h3 in body.find_all("h3"):
            t = norm_space(h3.get_text(" "))
            if t in by_sub and _season_closed(h3):
                by_sub[t].has_schedule = True

        for table in body.find_all("table"):
            if subs:
                sub = _sub_hall_for(table, subs)
                if sub is None:
                    raise ParseError(f"{prefix}: table without a known sub-heading")
                hall = by_sub[sub]
            else:
                hall = sec_halls[0]
            hid = hall.result.hall_id
            ctx = _context(table)
            if re.search(r"ordinarie", ctx, re.I):
                m = SEASON_RE.search(ctx)
                if m:
                    w1, y1, w2, y2 = map(int, m.groups())
                    s_from, s_to = iso_week_monday(y1, w1), iso_week_sunday(y2, w2)
                else:
                    m = SEASON_END_RE.search(ctx)
                    if not m:
                        raise ParseError(f"{hid}: no season stated for ordinary table: {ctx!r}")
                    s_from, s_to = None, iso_week_sunday(int(m.group(2)), int(m.group(1)))
                rows = _parse_rows(table, hid, warnings)
                hall.templates.append((s_from, s_to, [Slot(*r) for r in rows]))
                hall.has_schedule = True
                continue
            rng = _parse_range(ctx, today)
            if rng is None:
                raise ParseError(f"{hid}: unrecognised table {ctx!r}")
            hall.has_schedule = True
            start, end = rng
            if end < today:
                continue  # an old holiday block
            rows = _parse_rows(table, hid, warnings)
            d = start
            while d <= end:
                hall.dated.setdefault(d, [])
                for wd, s, e, stick, note in rows:
                    if wd == d.weekday():
                        hall.dated[d].append(Session(d.isoformat(), s, e, stick, SOURCE_URL, note))
                d += timedelta(days=1)

        # "Avvikande öppettider" apply to every hall in the section.
        for h3 in body.find_all("h3"):
            if not norm_space(h3.get_text(" ")).lower().startswith("avvikande"):
                continue
            ul = h3.find_next_sibling(["ul", "ol"])
            items = ul.find_all("li") if ul else h3.find_next_siblings("p")
            for li in items:
                _apply_exception(norm_space(li.get_text(" ")), sec_halls, today, warnings)

        for h in sec_halls:
            if not h.has_schedule:
                raise ParseError(f"{h.result.hall_id}: no schedule table and no closed notice")

    # Adapter-wide covered range: the union of the seasons, cut to the horizon.
    all_t = [t for h in halls.values() for t in h.templates]
    if not all_t:
        raise ParseError("No ordinary schedule tables found")
    froms = [f for f, _, _ in all_t]
    s_from = None if any(f is None for f in froms) else min(froms)
    window = recurring_window(today, s_from, max(t for _, t, _ in all_t))
    empty_reason = None
    if window is None:
        window = recurring_window(today)
        empty_reason = "The ordinary season stated on the page is over or has not started"
    lo, hi = window

    for h in halls.values():
        sessions: list[Session] = []
        overridden = set(h.dated) | h.closed
        for f, t, slots in h.templates:
            w = recurring_window(today, f, t)
            if w and empty_reason is None:
                sessions += expand_weekly(slots, max(w[0], lo), min(w[1], hi), SOURCE_URL, skip=overridden)
        for d, ss in h.dated.items():
            if lo <= d <= hi and d not in h.closed:
                sessions += ss
        seen = set()
        for s in sorted(sessions, key=lambda s: (s.date, s.start, s.end)):
            key = (s.date, s.start, s.end, s.stick)
            if key not in seen:
                seen.add(key)
                h.result.sessions.append(s)

    m = UPDATED_RE.search(soup.get_text(" "))
    updated = None
    if m:
        updated = sv_date(m.group(1), m.group(2), today, m.group(3)).isoformat() + "T00:00"

    return AdapterResult(
        covers_from=lo.isoformat(), covers_to=hi.isoformat(), source_updated=updated,
        halls={k: h.result for k, h in halls.items()}, warnings=warnings, empty_reason=empty_reason,
    )


def _apply_exception(line: str, halls: list[_Hall], today: date, warnings: list[str]) -> None:
    m = EXC_RE.match(line)
    if not m:
        warnings.append(f"{halls[0].result.hall_id}: unreadable exception {line!r}")
        return
    d1, d2, mon, year, rest = m.groups()
    try:
        first = sv_date(d1, mon, today, year)
        last = sv_date(d2, mon, today, year) if d2 else first
    except ParseError:
        warnings.append(f"{halls[0].result.hall_id}: unreadable exception date {line!r}")
        return
    horizon = today + timedelta(days=RECURRING_HORIZON_DAYS - 1)
    if last < today or first > horizon:
        return
    dates = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    try:
        ranges = time_ranges(rest)
    except BadTimeRange as exc:
        warnings.append(f"{halls[0].result.hall_id}: unreadable exception {line!r} ({exc})")
        return
    for h in halls:
        if ranges:
            stick = stick_from_text(rest)
            for d in dates:
                h.dated[d] = [Session(d.isoformat(), s, e, stick or STICK_UNKNOWN, SOURCE_URL,
                                      None if stick else rest) for s, e in ranges]
        elif CLOSED_RE.search(rest):
            h.closed.update(dates)
        else:
            warnings.append(f"{h.result.hall_id}: exception not understood, weekly times kept: {line!r}")
