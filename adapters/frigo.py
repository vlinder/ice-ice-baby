"""Shared parser for FRI-GO booking lists (`/bokning/lista.aspx`).

A list URL fixes the period (`dagar`, counted from today), the booking types
(`typer`), the columns (`falt`) and optionally one facility (`obj`). The `c`
parameter is a checksum over the query, so the URL cannot be changed: other
values answer "Behörighet saknas".

Markup (Nov 2026):

    <table id="tblLista">
      <tr><th colspan=6><span>Måndag&nbsp;2026-11-02</span></th></tr>
      <tr><td>14:30-16:20</td><td>Träkvistavallen/Vikingahallen</td>
          <td>Allmänhetens åkning</td><td>Alltid Hjälm, Med klubba</td>...</tr>

The cells follow the order of `falt`. An empty list is a single cell
"Inga aktuella bokningar att visa".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from .common import (
    STICK_UNKNOWN, AdapterResult, HallResult, ParseError, Session,
    hhmm, http_get, norm_space, slugify, stick_from_text,
)

DATE_RE = re.compile(r"^[A-Za-zÅÄÖåäö]+\s+(\d{4})-(\d{2})-(\d{2})$")
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")
EMPTY_TEXT = "inga aktuella bokningar att visa"
# Columns that name the facility / the booking, by their `falt` key.
PLACE_FIELDS = ("fullnamn", "lokal", "obj")
NAME_FIELDS = ("namn", "boktyp")


@dataclass(frozen=True)
class Hall:
    """A hall in a list. `place` is matched against the facility column (as a
    prefix, case-insensitive); lists made for one facility (`obj=`) can leave it
    empty and give `url` instead."""
    hall_id: str
    name: str
    rink: str | None = None
    place: str | None = None
    url: str | None = None


def fields(url: str) -> list[str]:
    q = parse_qs(urlparse(url).query)
    return q["falt"][0].split(",")


def days(url: str) -> int:
    return int(parse_qs(urlparse(url).query)["dagar"][0])


def fetch_lists(urls: list[str]) -> dict[str, str]:
    return {u: http_get(u) for u in urls}


def _resolve(place: str, halls: list[Hall], muni: str) -> tuple[str, str, str | None]:
    for h in halls:
        if h.place and place.lower().startswith(h.place.lower()):
            return h.hall_id, h.name, h.rink
    parts = [p.strip() for p in place.split("/") if p.strip()] or [place]
    return f"{muni}-" + slugify(" ".join(parts)), parts[0], (parts[1] if len(parts) > 1 else None)


def parse_lists(raw: dict[str, str], today: date, *, halls: list[Hall], muni: str) -> AdapterResult:
    """raw maps list URL -> list HTML."""
    if not raw:
        raise ParseError("No lists fetched")
    results: dict[str, HallResult] = {}
    warnings: list[str] = []
    ends: list[str] = []
    empty = 0

    for url, html in raw.items():
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table", id="tblLista")
        if table is None:
            text = norm_space(soup.get_text(" "))[:200]
            raise ParseError(f"No booking list in {url} – page says: {text!r}")
        cols = fields(url)
        if "tid" not in cols:
            raise ParseError(f"List {url} has no time column")
        i_time = cols.index("tid")
        i_place = next((cols.index(f) for f in PLACE_FIELDS if f in cols), None)
        i_name = next((cols.index(f) for f in NAME_FIELDS if f in cols), None)
        own = next((h for h in halls if h.url == url), None)
        if own is None and i_place is None:
            raise ParseError(f"List {url} names no facility and is not mapped to a hall")
        ends.append((today + timedelta(days=days(url) - 1)).isoformat())
        if own:
            # A one-facility list: the hall exists even when it has no times.
            results.setdefault(own.hall_id, HallResult(hall_id=own.hall_id, name=own.name, rink=own.rink))

        current: date | None = None
        rows = table.find_all("tr")
        if len(rows) == 1 and norm_space(rows[0].get_text(" ")).lower() == EMPTY_TEXT:
            empty += 1
            continue
        for tr in rows:
            th = tr.find("th")
            if th is not None:
                m = DATE_RE.match(norm_space(th.get_text(" ")))
                if not m:
                    raise ParseError(f"Unexpected date heading {th.get_text()!r} in {url}")
                current = date(int(m[1]), int(m[2]), int(m[3]))
                continue
            cells = [norm_space(td.get_text(" ")) for td in tr.find_all("td")]
            if not cells:
                continue
            tm = TIME_RE.match(cells[i_time]) if len(cells) > i_time else None
            if current is None or tm is None or len(cells) < len(cols):
                warnings.append(f"could not read row: {cells}")
                continue
            if i_name is not None and "allmänhet" not in cells[i_name].lower():
                warnings.append(f"row is not public skating: {cells}")
                continue
            s, e = hhmm(tm[1], tm[2]), hhmm(tm[3], tm[4])
            if e <= s:
                raise ParseError(f"end before start in row {cells}")
            if own:
                hid, name, rink = own.hall_id, own.name, own.rink
            else:
                hid, name, rink = _resolve(cells[i_place], halls, muni)
            rest = [c for i, c in enumerate(cells) if i not in (i_time, i_place, i_name) and c]
            info = ", ".join(rest)
            stick = stick_from_text(info)
            hall = results.setdefault(hid, HallResult(hall_id=hid, name=name, rink=rink))
            sess = Session(date=current.isoformat(), start=s, end=e, stick=stick or STICK_UNKNOWN,
                           source_url=url, note=None if stick else (info or None))
            if all((x.date, x.start, x.end, x.stick) != (sess.date, sess.start, sess.end, sess.stick)
                   for x in hall.sessions):
                hall.sessions.append(sess)

    return AdapterResult(
        covers_from=today.isoformat(),
        covers_to=max(ends),
        source_updated=None,
        halls=results,
        warnings=warnings,
        empty_reason=("Every booking list says 'Inga aktuella bokningar att visa'"
                      if empty == len(raw) else None),
    )
