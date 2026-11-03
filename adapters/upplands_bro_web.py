"""Upplands-Bro kommun: upplands-bro.se/is "Åka skidor och skridskor".

Page layout (as of Nov 2026, Sitevision): ``<h3>Skridskor på isbanan på
Kungsängens IP</h3>`` (the outdoor, artificially frozen bandy rink) is followed by
a collapsible box "Öppettider på isbanan Kungsängen" holding dated tables
("Öppettider för isbanan vecka 9", rows "Måndag 23/2 | 10.00 - 21.00", no year),
and a few paragraphs of text. The ice is normally open from jullovet to sportlovet.

The section is in one of three states:

* **Closed** – the text says "Tack för den här säsongen!" and the box still holds last
  season's last table. Every dated row must lie in the past; the hall is returned with no
  sessions and an ``empty_reason``.
* **Pre-season** – the text says "Nu närmar sig is-säsongen … Planerad öppning av is-banan
  är lucia." Rows dated today or later are read as sessions; if there are none, the hall is
  returned with no sessions and an ``empty_reason``, covering the days up to the planned
  opening when it can be read ("lucia", "13 december").
* **In season** – no notice; the dated rows from today on are the sessions, and ``covers``
  is the days the tables list.

A table whose caption names a week ("vecka 9") is dated by that week, not by its weekday
names: in Nov 2026 the left-over table says "vecka 9" over "Måndag 22/2 – Söndag 28/2",
i.e. last season's sportlov (2026, week 9 bar Sunday 22/2) with the dates edited but the
weekday names kept. A table entirely in the past is ignored; a current or future one whose
dates, weekdays and week disagree is skipped with a warning. A first cell that is not "Weekday D/M" raises ParseError, as
does a pre-season notice whose planned opening has passed with no times listed, and an
in-season page with no times.

Further down, "Status skidspår och skridskoisar" has tables with dates like
20260123 / 2026-02-24 (last grooming); they are not opening hours and lie
outside the Kungsängen section, so they are never read.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from bs4 import BeautifulSoup, Tag

from .common import (
    STICK_UNKNOWN, AdapterResult, BadTimeRange, HallResult, ParseError, Session, http_get,
    norm_space, recurring_window, sv_date, time_ranges, weekday_number,
)

NAME = "upplands-bro-web"
MUNICIPALITY = "Upplands-Bro"
SOURCE_URL = "https://www.upplands-bro.se/is"

HALL_ID = "upplands-bro-kungsangens-ip"
HALL_NAME = "Kungsängens IP"
SECTION_RE = re.compile(r"isbanan\s+på\s+Kungsängens\s+IP", re.I)
CLOSED_RE = re.compile(r"Tack\s+för\s+den\s+här\s+säsongen", re.I)
PRESEASON_RE = re.compile(r"närmar\s+sig\s+is-?säsongen|Planerad\s+öppning", re.I)
OPENING_RE = re.compile(r"Planerad\s+öppning\s+av\s+is-?banan\s+är\s+([^.!]+)", re.I)
OPENING_DATE_RE = re.compile(r"(\d{1,2})\s+([a-zåäö]+)", re.I)
ROW_DATE_RE = re.compile(r"^([a-zåäö]+)\s+(\d{1,2})/(\d{1,2})\b", re.I)
WEEK_RE = re.compile(r"vecka\s+(\d{1,2})", re.I)
NO_TIMES_RE = re.compile(r"\bstängt\b", re.I)
UPDATED_RE = re.compile(r"Uppdaterad:?\s*(\d{1,2})\s+([a-zåäö]+)\s+(\d{4})", re.I)


def fetch() -> dict[str, str]:
    return {"page": http_get(SOURCE_URL)}


def _section(soup: BeautifulSoup) -> list[Tag]:
    """Elements between the Kungsängens IP heading and the next h2/h3."""
    head = None
    for h in soup.find_all(["h2", "h3"]):
        if SECTION_RE.search(norm_space(h.get_text(" "))):
            head = h
            break
    if head is None:
        raise ParseError("Kungsängens IP heading not found – has the page layout changed?")
    out = []
    for el in head.find_all_next(True):
        if el.name in ("h2", "h3"):
            break
        out.append(el)
    return out


def _row_date(day_word: str, d: int, m: int, today: date) -> date | None:
    """Rows have no year; pick the year (around today) where the weekday matches."""
    wd = weekday_number(day_word)
    best = None
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            cand = date(y, m, d)
        except ValueError:
            continue
        if wd is not None and cand.weekday() != wd:
            continue
        if best is None or abs((cand - today).days) < abs((best - today).days):
            best = cand
    return best


def _date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _week_year(week: int, cells: list[tuple[str, int, int]], today: date) -> int | None:
    """The year (around today) in which most of the listed (weekday, day, month) fall in ISO
    week ``week``; ties go to the year where more weekday names fit, then to the nearest.
    None if they never fall in that week."""
    best, best_key = None, None
    for y in (today.year - 1, today.year, today.year + 1):
        xs = [(wd, x) for wd, d, m in cells if (x := _date(y, m, d))]
        hits = sum(1 for _, x in xs if tuple(x.isocalendar())[:2] == (y, week))
        if not hits:
            continue
        fits = sum(1 for wd, x in xs if weekday_number(wd) == x.weekday())
        key = (hits, fits, -min(abs((x - today).days) for _, x in xs))
        if best_key is None or key > best_key:
            best, best_key = y, key
    return best


def _rows(els: list[Tag], today: date, warnings: list[str]) -> list[tuple[date, str]]:
    """(date, time cell) for every usable row of the section's tables.

    A table whose caption names a week is dated by that week: the year is the one in which
    the listed days fall in it. Without a week, each row's year is the one where its weekday
    matches. A dated table that lies entirely in the past is dropped silently, even if its
    weekday names don't fit (left-over tables get their dates edited); one that is current
    or ahead must agree with itself, or it is skipped with a warning."""
    out = []
    for table in (el for el in els if el.name == "table"):
        cap = table.find("caption")
        caption = norm_space(cap.get_text(" ")) if cap else ""
        cells = []
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if not tds:
                continue
            cell = norm_space(tds[0].get_text(" "))
            m = ROW_DATE_RE.match(cell)
            if not m:
                raise ParseError(f"Kungsängens IP: unexpected row {cell!r} in {caption or 'a table'!r}")
            cells.append((cell, m.group(1), int(m.group(2)), int(m.group(3)),
                          norm_space(" ".join(td.get_text(" ") for td in tds[1:]))))
        if not cells:
            continue

        wk = WEEK_RE.search(caption)
        if wk:
            week = int(wk.group(1))
            y = _week_year(week, [(wd, d, mo) for _, wd, d, mo, _ in cells], today)
            if y is None:
                warnings.append(f"{HALL_ID}: table {caption!r} lists {cells[0][0]!r}–{cells[-1][0]!r}, "
                                f"which is never week {week} – times not used")
                continue
            dated = [(cell, wd, _date(y, mo, d), t) for cell, wd, d, mo, t in cells]
            if all(x is not None and x < today for _, _, x, _ in dated):
                continue  # an old table
            bad = [cell for cell, wd, x, _ in dated
                   if x is None or tuple(x.isocalendar())[:2] != (y, week) or weekday_number(wd) != x.weekday()]
            if bad:
                warnings.append(f"{HALL_ID}: table {caption!r}: {', '.join(map(repr, bad))} "
                                f"does not fit week {week} of {y} – times not used")
                continue
            out.extend((x, t) for _, _, x, t in dated)
            continue

        for cell, wd, d, mo, t in cells:
            x = _row_date(wd, d, mo, today)
            if x is None:
                warnings.append(f"{HALL_ID}: {cell!r} is not a real date in any nearby year – row skipped")
                continue
            out.append((x, t))
    return out


def _sessions(rows: list[tuple[date, str]], warnings: list[str]) -> list[Session]:
    out = []
    for d, txt in rows:
        try:
            ranges = time_ranges(txt)
        except BadTimeRange as exc:
            warnings.append(f"{HALL_ID}: {d.isoformat()}: unreadable time {exc}")
            continue
        if not ranges and not NO_TIMES_RE.search(txt):
            warnings.append(f"{HALL_ID}: {d.isoformat()}: no time in {txt!r}")
        for s, e in ranges:
            out.append(Session(date=d.isoformat(), start=s, end=e, stick=STICK_UNKNOWN,
                               source_url=SOURCE_URL))
    return out


def _planned_opening(text: str, today: date) -> tuple[str, date | None] | None:
    """The planned-opening phrase ("lucia") and its date, if it can be read."""
    m = OPENING_RE.search(text)
    if not m:
        return None
    phrase = norm_space(m.group(1))
    if re.fullmatch(r"(till\s+)?lucia", phrase, re.I):
        y = today.year if (today.month, today.day) <= (12, 13) else today.year + 1
        return phrase, date(y, 12, 13)
    dm = OPENING_DATE_RE.fullmatch(phrase)
    if dm:
        try:
            return phrase, sv_date(dm.group(1), dm.group(2), today)
        except ParseError:
            pass
    return phrase, None


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    soup = BeautifulSoup(raw["page"], "lxml")
    els = _section(soup)
    text = norm_space(" ".join(el.get_text(" ") for el in els if el.name in ("p", "caption", "h4")))
    closed = CLOSED_RE.search(text)
    preseason = PRESEASON_RE.search(text)

    warnings: list[str] = []
    rows = _rows(els, today, warnings)
    future = [(d, t) for d, t in rows if d >= today]

    m = UPDATED_RE.search(soup.get_text(" "))
    updated = sv_date(m.group(1), m.group(2), today, m.group(3)).isoformat() + "T00:00" if m else None
    hall = HallResult(hall_id=HALL_ID, name=HALL_NAME, rink=None)

    if closed:
        if future:
            raise ParseError(f"Kungsängens IP: a row dated {future[0][0].isoformat()} is not in the "
                             "past although the page says the season is over")
        lo, hi = recurring_window(today)
        return AdapterResult(
            covers_from=lo.isoformat(), covers_to=hi.isoformat(), source_updated=updated,
            halls={HALL_ID: hall}, warnings=warnings,
            empty_reason="Season over: the page says 'Tack för den här säsongen!' and lists no times",
        )

    if future:
        hall.sessions = _sessions(future, warnings)
        lo = max(today, min(d for d, _ in future))
        hi = max(d for d, _ in future)
        return AdapterResult(covers_from=lo.isoformat(), covers_to=hi.isoformat(),
                             source_updated=updated, halls={HALL_ID: hall}, warnings=warnings)

    if preseason:
        opening = _planned_opening(text, today)
        season_from = opening[1] if opening else None
        if season_from is not None and season_from <= today:
            raise ParseError(f"Kungsängens IP: the planned opening ({opening[0]!r}) has passed but "
                             "the page still lists no times")
        win = recurring_window(today, season_to=season_from - timedelta(days=1) if season_from else None)
        reason = "Season not started: the page says the ice is being prepared"
        if opening:
            reason += f" (planned opening: {opening[0]})"
        return AdapterResult(
            covers_from=win[0].isoformat(), covers_to=win[1].isoformat(), source_updated=updated,
            halls={HALL_ID: hall}, warnings=warnings, empty_reason=reason + " and lists no times",
        )

    raise ParseError("Kungsängens IP: no season notice and no current times – has the page layout changed?")
