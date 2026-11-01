"""Tyresö kommun: public skating board in the Rbok booking system.

tyreso.se links to an "informationstavla" (information board) in Rbok that
lists upcoming public-skating times for both Tyresö ishall and Fontanahallen.
The board page itself is an empty shell that loads two endpoints:

    /Informationstavla/_Item/Resurser?id=<board>   JSON: {"Resurser": [...], ...}
    /Informationstavla/_Item?id=<board>&index=<n>  HTML table rows

Each row: "Ons 04 Nov" | "16:00 - 16:45" | "Tyresövallen / Tyresö Ishall / Ishall" | "Med klubba"

The board lists times from today onwards for roughly two weeks; the year is
not shown and is inferred. Holiday (lov) times also appear on the board.
"""
from __future__ import annotations

import json
import re
from datetime import date

from bs4 import BeautifulSoup

from .common import (
    STICK_SHARED, STICK_UNKNOWN, STICK_WITH, STICK_WITHOUT,
    AdapterResult, HallResult, ParseError, Session,
    hhmm, http_get, infer_year, month_number, norm_space, slugify,
)

NAME = "tyreso-rbok"
MUNICIPALITY = "Tyresö"
BASE = "https://tyreso.rbok.se"
BOARD_ID = "fbe27841-479e-4387-8e40-9b6a158436c4"
SOURCE_URL = f"{BASE}/informationstavla?id={BOARD_ID}"
# The municipality's own page that links to the board, shown as context.
INFO_URL = "https://www.tyreso.se/uppleva--gora/natur-och-friluftsliv/aka-skridskor/allmanhetens-akning.html"

DATE_RE = re.compile(r"^[A-Za-zÅÄÖåäö]+\.?\s+(\d{1,2})\s+([A-Za-zÅÄÖåäö]+)\.?(?:\s+(\d{4}))?$")
TIME_RE = re.compile(r"^(\d{1,2})[:.](\d{2})\s*[–\-]\s*(\d{1,2})[:.](\d{2})$")


def fetch() -> dict[str, str]:
    meta = http_get(f"{BASE}/Informationstavla/_Item/Resurser", params={"id": BOARD_ID})
    count = max(1, len(json.loads(meta).get("Resurser") or []))
    raw = {"resurser": meta}
    for i in range(count):
        raw[f"item{i}"] = http_get(f"{BASE}/Informationstavla/_Item", params={"id": BOARD_ID, "index": i})
    return raw


def hall_id(resource: str) -> str:
    parts = [p.strip() for p in resource.split("/") if p.strip()]
    key = parts[1] if len(parts) >= 2 else parts[0]
    return "tyreso-" + slugify(key)


def _stick(info: str) -> tuple[str, str | None]:
    low = info.lower()
    if "utan klubba" in low:
        return STICK_WITHOUT, None
    if "med klubba" in low:
        return STICK_WITH, None
    if "delad" in low:
        return STICK_SHARED, None
    return STICK_UNKNOWN, (info or None)


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    items = [v for k, v in sorted(raw.items()) if k.startswith("item")]
    if not items:
        raise ParseError("No board pages fetched")

    halls: dict[str, HallResult] = {}
    warnings: list[str] = []
    seen: set[tuple] = set()
    tables = 0

    for html in items:
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table")
        if table is None:
            raise ParseError("Board page has no table – has Rbok changed its layout?")
        tables += 1
        head = [norm_space(td.get_text()).lower() for td in table.select("thead td, thead th")]
        try:
            i_date, i_time, i_res = head.index("datum"), head.index("tid"), head.index("resurs")
        except ValueError:
            raise ParseError(f"Unexpected table header: {head}")
        i_info = head.index("information") if "information" in head else None

        for tr in table.select("tbody tr"):
            cells = [norm_space(td.get_text(" ")) for td in tr.find_all("td")]
            if len(cells) < len(head):
                warnings.append(f"short row: {cells}")
                continue
            dm, tm = DATE_RE.match(cells[i_date]), TIME_RE.match(cells[i_time])
            if not dm or not tm:
                warnings.append(f"could not read row: {cells}")
                continue
            d, mon_word, y = dm.groups()
            mon = month_number(mon_word)
            year = int(y) if y else infer_year(mon, int(d), today)
            day = date(year, mon, int(d)).isoformat()
            s, e = hhmm(tm[1], tm[2]), hhmm(tm[3], tm[4])
            if e <= s:
                raise ParseError(f"end before start in row {cells}")
            resource = cells[i_res]
            info = cells[i_info] if i_info is not None else ""
            key = (day, s, e, resource, info)
            if key in seen:
                continue  # board pages repeat the same rows
            seen.add(key)
            hid = hall_id(resource)
            parts = [p.strip() for p in resource.split("/")]
            hall = halls.setdefault(hid, HallResult(
                hall_id=hid, name=parts[1] if len(parts) > 1 else parts[0], rink=parts[0]))
            stick, note = _stick(info)
            hall.sessions.append(Session(date=day, start=s, end=e, stick=stick, source_url=SOURCE_URL, note=note))

    all_dates = [s.date for h in halls.values() for s in h.sessions]
    return AdapterResult(
        covers_from=today.isoformat(),
        covers_to=max(all_dates + [today.isoformat()]),
        source_updated=None,  # the board carries no "last updated" stamp
        halls=halls,
        warnings=warnings,
    )
