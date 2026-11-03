"""Täby kommun: taby.se, "Skridskor och ishall".

Page layout (as of Nov 2026): under "Ishallar", one collapsible per hall
(<h3><a aria-controls="ID"><span>Hägernäs ishall</span></a></h3> + <div id="ID">).
Each body is a run of <p> lines with bold block headings:

    Vecka 44, höstlovet                    -> dated week: lines are that week's days
    Måndag 14.00–15.45 med klubba
    Lördag Stängd
    Vecka 45
    Måndag 12.30–14.15 med klubba
    Ordinarie tider, vecka 45–vecka 10     -> weekly template for that season
    Måndagar 15.00–15.45 utan klubba

followed by contact lines (phone, address). A paragraph "Allmänhetens åkning
2026" states the overall period ("26 oktober (höstlovet) till och med vecka 10").

Rules:
- A "Vecka NN" block covers Monday of that week up to its last listed weekday;
  blocks that ended before today are skipped unread.
- "Ordinarie tider, vecka A–vecka B" is expanded with `expand_weekly` over
  `recurring_window(today, Monday of A, Sunday of B)`; dates covered by a
  current "Vecka NN" block for the same hall override the template.
- `covers` is set per hall (Hägernäs lists single weeks, Tibble has a weekly
  template), and the adapter-wide range is their union. A hall whose blocks are
  all in the past gets its last block's (past) range, i.e. unknown from today.
- Each line carries its own stick wording ("med klubba"/"utan klubba"); there is
  no default. "Stängd" means no public skating that day.
- A day line whose time does not parse (e.g. the typo "13.300–15.15") is
  skipped with a warning.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from bs4 import BeautifulSoup, Tag

from .common import (
    STICK_UNKNOWN, TIME_RANGE_RE, AdapterResult, BadTimeRange, HallResult, ParseError,
    Session, Slot, expand_weekly, http_get, iso_week_monday, iso_week_sunday, norm_space,
    recurring_window, slugify, stick_from_text, sv_date, time_ranges, week_year,
    weekday_number,
)

NAME = "taby-web"
MUNICIPALITY = "Täby"
SOURCE_URL = "https://www.taby.se/uppleva-och-gora/idrott-och-motion/skridskor-och-ishall"

HALLS = ["Hägernäs ishall", "Tibble ishall"]

WEEK_RE = re.compile(r"^vecka\s+(\d{1,2})(?:\s*,.*)?$", re.I)
WEEKLY_RE = re.compile(r"^ordinarie\s+tider\b.*?vecka\s+(\d{1,2})\s*[–—-]\s*(?:vecka\s+)?(\d{1,2})\b", re.I)
# Something that looks like a block heading we don't know ("Jullovet", "Vecka 52–1").
HEADING_LIKE_RE = re.compile(r"^(vecka|ordinarie)\b|lov(et)?\b", re.I)
CLOSED_RE = re.compile(r"^(stängd|stängt|inställd|ingen\b.*)", re.I)
# Swedish phone numbers ("08-555 592 64") would otherwise read as a time range.
PHONE_RE = re.compile(r"\b0\d{1,3}\s*-\s*\d{2,3}(?:\s?\d{2,3}){1,3}\b")
UPDATED_RE = re.compile(r"senast uppdaterad:?\s*(\d{1,2})\s+([a-zåäö]+)\s+(\d{4})", re.I)


def fetch() -> dict[str, str]:
    return {"page": http_get(SOURCE_URL)}


def hall_id(name: str) -> str:
    return "taby-" + slugify(name)


def _body(soup: BeautifulSoup, name: str) -> Tag:
    for h in soup.find_all("h3"):
        if norm_space(h.get_text(" ")).lower() == name.lower():
            a = h.find("a", attrs={"aria-controls": True})
            body = soup.find(id=a["aria-controls"]) if a else h.find_next_sibling("div")
            if body is None:
                raise ParseError(f"No body for {name!r}")
            return body
    raise ParseError(f"Hall heading {name!r} not found – layout changed?")


def _lines(body: Tag) -> list[str]:
    out = []
    for p in body.find_all(["p", "li", "h4"]):
        for br in p.find_all("br"):
            br.replace_with("\n")
        for part in p.get_text().split("\n"):
            t = norm_space(part)
            if t:
                out.append(t)
    return out


def _day_line(line: str, where: str, warnings: list[str]) -> tuple[int, list[tuple[str, str, str, str | None]]] | None:
    """'Måndag 14.00–15.45 med klubba' -> (weekday, [(start, end, stick, note)]).
    None if the line does not start with a weekday."""
    first, _, rest = line.partition(" ")
    wd = weekday_number(first)
    if wd is None:
        return None
    rest = rest.strip()
    if CLOSED_RE.match(rest) and not re.search(r"\d", rest):
        return wd, []
    try:
        ranges = time_ranges(rest)
    except BadTimeRange as exc:
        warnings.append(f"{where}: could not read {line!r} ({exc})")
        return wd, []
    if not ranges:
        warnings.append(f"{where}: could not read {line!r}")
        return wd, []
    wording = norm_space(TIME_RANGE_RE.sub(" ", rest)).strip(" ,.-–")
    stick = stick_from_text(wording)
    note = None
    if stick is None:
        stick, note = STICK_UNKNOWN, (wording or None)
    return wd, [(s, e, stick, note) for s, e in ranges]


def _parse_hall(name: str, lines: list[str], today: date, warnings: list[str]) -> tuple[HallResult, date, date]:
    hid = hall_id(name)
    hall = HallResult(hall_id=hid, name=name, rink=None)
    dated: dict[date, list] = {}            # day -> [(s, e, stick, note)] from current week blocks
    week_spans: list[tuple[date, date]] = []
    past_spans: list[tuple[date, date]] = []
    slots: list[Slot] = []
    window: tuple[date, date] | None = None
    season_seen = False
    not_yet: tuple[date, date] | None = None

    # block state
    kind: str | None = None                 # "week" | "weekly" | "past" | "unknown"
    monday: date | None = None
    listed: list[int] = []

    def close() -> None:
        if kind in ("week", "past") and monday is not None and listed:
            span = (monday, monday + timedelta(days=max(listed)))
            (week_spans if kind == "week" else past_spans).append(span)

    for line in lines:
        wm, rm = WEEK_RE.match(line), WEEKLY_RE.match(line)
        if wm or rm:
            close()
            listed = []
            if wm:
                w = int(wm.group(1))
                monday = iso_week_monday(week_year(w, today), w)
                kind = "past" if monday + timedelta(days=6) < today else "week"
            else:
                w1, w2 = int(rm.group(1)), int(rm.group(2))
                s_from = iso_week_monday(week_year(w1, today), w1)
                s_to = iso_week_sunday(s_from.isocalendar()[0] + (1 if w2 < w1 else 0), w2)
                if season_seen:
                    raise ParseError(f"{hid}: more than one 'Ordinarie tider' block")
                season_seen = True
                window = recurring_window(today, s_from, s_to)
                kind, monday = ("weekly" if window else "past"), None
                if not window and s_to < today:
                    past_spans.append((s_from, s_to))
                elif not window:
                    # season starts beyond the horizon: no public times until then
                    not_yet = recurring_window(today)
            continue
        first = line.partition(" ")[0]
        if weekday_number(first) is not None:
            if kind is None or kind == "unknown":
                if TIME_RANGE_RE.search(line):
                    warnings.append(f"{hid}: day line outside a known block: {line!r}")
                continue
            wd = weekday_number(first)
            if kind in ("week", "past"):
                listed.append(wd)
            if kind == "past":
                continue
            where = f"{hid} {monday + timedelta(days=wd)}" if kind == "week" else f"{hid} (weekly)"
            parsed = _day_line(line, where, warnings)
            assert parsed is not None
            _, times = parsed
            if kind == "week":
                day = monday + timedelta(days=wd)
                dated.setdefault(day, []).extend(times)
            else:
                slots += [Slot(wd, s, e, stick, note) for s, e, stick, note in times]
            continue
        if HEADING_LIKE_RE.search(line) and len(line) < 40 and not line.endswith(".") \
                and not TIME_RANGE_RE.search(line):
            close()
            kind, monday, listed = "unknown", None, []
            warnings.append(f"{hid}: unrecognised block heading {line!r}")
            continue
        if TIME_RANGE_RE.search(PHONE_RE.sub(" ", line)):
            warnings.append(f"{hid}: unexpected line with a time: {line!r}")
    close()

    def add(d: date, s: str, e: str, stick: str, note: str | None) -> None:
        if s < "05:00" or e > "24:00":
            warnings.append(f"{hid}: {d} {s}–{e} outside 05–24, skipped")
            return
        key = (d.isoformat(), s, e, stick)
        if any((x.date, x.start, x.end, x.stick) == key for x in hall.sessions):
            return
        hall.sessions.append(Session(date=d.isoformat(), start=s, end=e, stick=stick,
                                     source_url=SOURCE_URL, note=note))

    overridden: set[date] = set()
    for a, b in week_spans:
        d = a
        while d <= b:
            overridden.add(d)
            for s, e, stick, note in dated.get(d, []):
                add(d, s, e, stick, note)
            d += timedelta(days=1)
    if window:
        for sess in expand_weekly(slots, window[0], window[1], SOURCE_URL, skip=overridden):
            add(date.fromisoformat(sess.date), sess.start, sess.end, sess.stick, sess.note)

    spans = week_spans + ([window] if window else []) + ([not_yet] if not_yet else [])
    if spans:
        lo, hi = min(x[0] for x in spans), max(x[1] for x in spans)
    elif past_spans:
        lo, hi = max(past_spans, key=lambda x: x[1])
    else:
        raise ParseError(f"{hid}: no 'Vecka NN' or 'Ordinarie tider' block found")
    hall.sessions.sort(key=lambda s: (s.date, s.start, s.end))
    hall.covers_from, hall.covers_to = lo.isoformat(), hi.isoformat()
    return hall, lo, hi


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    soup = BeautifulSoup(raw["page"], "lxml")
    warnings: list[str] = []
    halls: dict[str, HallResult] = {}
    ranges = []
    for name in HALLS:
        hall, lo, hi = _parse_hall(name, _lines(_body(soup, name)), today, warnings)
        halls[hall.hall_id] = hall
        ranges.append((lo, hi))

    updated = None
    m = UPDATED_RE.search(norm_space(soup.get_text(" ")))
    if m:
        updated = sv_date(m.group(1), m.group(2), today, m.group(3)).isoformat() + "T00:00"

    return AdapterResult(
        covers_from=min(r[0] for r in ranges).isoformat(),
        covers_to=max(r[1] for r in ranges).isoformat(),
        source_updated=updated,
        halls=halls,
        warnings=warnings,
    )
