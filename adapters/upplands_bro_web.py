"""Upplands-Bro kommun: upplands-bro.se/is "Åka skidor och skridskor".

Page layout (as of Nov 2026, Sitevision): ``<h3>Skridskor på isbanan på
Kungsängens IP</h3>`` (the outdoor, artificially frozen bandy rink) is followed by
a collapsible box "Öppettider på isbanan Kungsängen". Out of season that box
still holds last season's last table ("Öppettider för isbanan vecka 9", rows
"Måndag 23/2 | 10.00 - 21.00", no year) and the text "Tack för den här
säsongen!". The ice is normally open from jullovet to sportlovet.

This adapter only understands the closed state: when the Kungsängen section says
"Tack för den här säsongen!" and every dated row in it lies in the past, it
returns the hall with no sessions and an ``empty_reason``. Anything else (the
marker gone, or a row dated today or later) raises ParseError so that a human
writes a real parser for the in-season layout.

Further down, "Status skidspår och skridskoisar" has tables with dates like
20260123 / 2026-02-24 (last grooming); they are not opening hours and lie
outside the Kungsängen section, so they are never read.
"""
from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup, Tag

from .common import (
    AdapterResult, HallResult, ParseError, http_get, norm_space, recurring_window, sv_date,
    weekday_number,
)

NAME = "upplands-bro-web"
MUNICIPALITY = "Upplands-Bro"
SOURCE_URL = "https://www.upplands-bro.se/is"

HALL_ID = "upplands-bro-kungsangens-ip"
HALL_NAME = "Kungsängens IP"
SECTION_RE = re.compile(r"isbanan\s+på\s+Kungsängens\s+IP", re.I)
CLOSED_RE = re.compile(r"Tack\s+för\s+den\s+här\s+säsongen", re.I)
ROW_DATE_RE = re.compile(r"^([a-zåäö]+)\s+(\d{1,2})/(\d{1,2})\b", re.I)
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


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    soup = BeautifulSoup(raw["page"], "lxml")
    els = _section(soup)
    text = norm_space(" ".join(el.get_text(" ") for el in els if el.name in ("p", "caption", "h4")))
    if not CLOSED_RE.search(text):
        raise ParseError("Kungsängens IP: the 'Tack för den här säsongen' notice is gone – the page "
                         "probably shows this season's times now, which this adapter cannot read yet")
    for table in (el for el in els if el.name == "table"):
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if not tds:
                continue
            cell = norm_space(tds[0].get_text(" "))
            m = ROW_DATE_RE.match(cell)
            if not m:
                raise ParseError(f"Kungsängens IP: unexpected row {cell!r} next to the closed notice")
            d = _row_date(m.group(1), int(m.group(2)), int(m.group(3)), today)
            if d is None or d >= today:
                raise ParseError(f"Kungsängens IP: row {cell!r} is not in the past although the page "
                                 "says the season is over")

    lo, hi = recurring_window(today)
    m = UPDATED_RE.search(soup.get_text(" "))
    updated = sv_date(m.group(1), m.group(2), today, m.group(3)).isoformat() + "T00:00" if m else None
    return AdapterResult(
        covers_from=lo.isoformat(), covers_to=hi.isoformat(), source_updated=updated,
        halls={HALL_ID: HallResult(hall_id=HALL_ID, name=HALL_NAME, rink=None)},
        warnings=[],
        empty_reason="Season over: the page says 'Tack för den här säsongen!' and lists no times",
    )
