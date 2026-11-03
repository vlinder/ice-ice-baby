"""Shared parser for Rbok "informationstavla" (information board) pages.

Several municipalities publish public-skating times as an Rbok information
board. The board page itself is an empty shell that loads

    /Informationstavla/_Item?id=<board>&index=<n>   HTML table

Every index returns the same table (checked Nov 2026 on all boards in use), so
only index 0 is fetched. The table has the columns Datum | Tid | Resurs |
[Bokad av] | Information, e.g.

    "Ons 04 Nov" | "16:00 - 16:45" | "Tyresövallen / Tyresö Ishall / Ishall" | "Med klubba"

A board lists times from today onwards. It shows at most ROW_CAP rows, so when
a board is full its last day may be cut off: that day is then left out of
both the sessions and `covers`.

Each municipality has a small module (botkyrka_rbok.py, ...) with its board ids
and a table that maps the Resurs text to a hall.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from bs4 import BeautifulSoup

from .common import (
    STICK_SHARED, STICK_UNKNOWN, STICK_WITH, STICK_WITHOUT,
    AdapterResult, HallResult, ParseError, Session,
    hhmm, http_get, infer_year, month_number, norm_space, slugify, stick_from_text,
)

ROW_CAP = 100

DATE_RE = re.compile(r"^[A-Za-zÅÄÖåäö]+\.?\s+(\d{1,2})\s+([A-Za-zÅÄÖåäö]+)\.?(?:\s+(\d{4}))?$")
TIME_RE = re.compile(r"^(\d{1,2})[:.](\d{2})\s*[–\-]\s*(\d{1,2})[:.](\d{2})$")


@dataclass(frozen=True)
class Hall:
    """How a Resurs text maps to a hall. Matched as a prefix of the Resurs text;
    whatever follows the prefix (e.g. "Halvplan-A") becomes the session note."""
    resource: str
    hall_id: str
    name: str
    rink: str | None = None


def board_url(host: str, board: str) -> str:
    return f"https://{host}/informationstavla?id={board}"


def fetch_boards(host: str, boards: list[str]) -> dict[str, str]:
    return {board: http_get(f"https://{host}/Informationstavla/_Item", params={"id": board, "index": 0})
            for board in boards}


def _split(resource: str) -> list[str]:
    return [p.strip() for p in resource.split("/") if p.strip()]


def resolve(resource: str, halls: list[Hall], muni: str) -> tuple[str, str, str | None, str | None]:
    """Resurs text -> (hall_id, name, rink, note). Unknown resources get an id
    built from the first two parts."""
    parts = _split(resource)
    for h in halls:
        prefix = _split(h.resource)
        # Each part matches exactly or as a whole-word prefix: "Hall 2" matches "Hall 2 (fd Roplan)".
        if len(parts) >= len(prefix) and all(
                p.lower() == q.lower() or p.lower().startswith(q.lower() + " ")
                for p, q in zip(parts, prefix)):
            rest = " / ".join(parts[len(prefix):]) or None
            return h.hall_id, h.name, h.rink, rest
    # Unknown resource: still published, and flagged for review as a new hall.
    return f"{muni}-" + slugify(" ".join(parts[:2])), parts[0], (parts[1] if len(parts) > 1 else None), \
        (" / ".join(parts[2:]) or None)


def _stick(info: str) -> tuple[str, str | None]:
    stick = stick_from_text(info)
    return (stick, None) if stick else (STICK_UNKNOWN, info or None)


def _merge_halves(sessions: list[Session]) -> list[Session]:
    """Two halves of one rink at the same time, one with and one without sticks,
    is what the spec calls "shared" (delad is)."""
    groups: dict[tuple, list[Session]] = {}
    for s in sessions:
        groups.setdefault((s.date, s.start, s.end), []).append(s)
    out = []
    for group in groups.values():
        if len(group) > 1 and {s.stick for s in group} == {STICK_WITH, STICK_WITHOUT}:
            g = group[0]
            out.append(Session(date=g.date, start=g.start, end=g.end, stick=STICK_SHARED,
                               source_url=g.source_url, note=None))
            continue
        seen = set()
        for s in group:
            if (s.stick, s.note) not in seen:
                seen.add((s.stick, s.note))
                out.append(s)
    return out


def parse_boards(raw: dict[str, str], today: date, *, host: str, halls: list[Hall], muni: str) -> AdapterResult:
    """raw maps board id -> board HTML (index 0)."""
    if not raw:
        raise ParseError("No board pages fetched")

    results: dict[str, HallResult] = {}
    warnings: list[str] = []
    board_ends: list[str] = []
    seen: set[tuple] = set()

    for board, html in raw.items():
        url = board_url(host, board)
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table")
        if table is None:
            raise ParseError(f"Board {board} has no table – has Rbok changed its layout?")
        head = [norm_space(td.get_text()).lower() for td in table.select("thead td, thead th")]
        try:
            i_date, i_time, i_res = head.index("datum"), head.index("tid"), head.index("resurs")
        except ValueError:
            raise ParseError(f"Unexpected table header on board {board}: {head}") from None
        i_info = head.index("information") if "information" in head else None
        i_by = head.index("bokad av") if "bokad av" in head else None

        rows = table.select("tbody tr")
        board_sessions: list[tuple[str, str, str, str, Session]] = []
        for tr in rows:
            cells = [norm_space(td.get_text(" ")) for td in tr.find_all("td")]
            if len(cells) < len(head):
                warnings.append(f"short row {' | '.join(cells)!r}")
                continue
            dm, tm = DATE_RE.match(cells[i_date]), TIME_RE.match(cells[i_time])
            if not dm or not tm:
                hid = resolve(cells[i_res], halls, muni)[0]
                warnings.append(f"{hid}: could not read row {' | '.join(cells)!r}")
                continue
            if i_by is not None and "allmänhet" not in cells[i_by].lower():
                hid = resolve(cells[i_res], halls, muni)[0]
                warnings.append(f"{hid}: row not booked as public skating {' | '.join(cells)!r}")
                continue
            d, mon_word, y = dm.groups()
            mon = month_number(mon_word)
            year = int(y) if y else infer_year(mon, int(d), today)
            day = date(year, mon, int(d)).isoformat()
            s, e = hhmm(tm[1], tm[2]), hhmm(tm[3], tm[4])
            if e <= s:
                raise ParseError(f"end before start in row {cells}")
            info = cells[i_info] if i_info is not None else ""
            key = (day, s, e, cells[i_res], info)
            if key in seen:
                continue
            seen.add(key)
            hid, name, rink, part = resolve(cells[i_res], halls, muni)
            stick, note = _stick(info)
            if part:
                note = f"{part}: {note}" if note else part
            board_sessions.append((hid, name, rink, day, Session(
                date=day, start=s, end=e, stick=stick, source_url=url, note=note)))

        days = sorted({x[3] for x in board_sessions})
        end = days[-1] if days else today.isoformat()
        if len(rows) >= ROW_CAP and days:
            # A full board may have cut its last day short: don't claim that day.
            end = (date.fromisoformat(days[-1]) - timedelta(days=1)).isoformat()
        board_ends.append(end)
        board_halls = set()
        for hid, name, rink, day, sess in board_sessions:
            hall = results.setdefault(hid, HallResult(hall_id=hid, name=name, rink=rink))
            board_halls.add(hid)
            if day <= end:
                hall.sessions.append(sess)
        if len(raw) > 1:
            # Boards reach different horizons; give each board's halls their own range.
            for hid in board_halls:
                h = results[hid]
                h.covers_from = today.isoformat()
                h.covers_to = max(h.covers_to or end, end)

    for h in results.values():
        h.sessions = _merge_halves(h.sessions)

    return AdapterResult(
        covers_from=today.isoformat(),
        covers_to=max([today.isoformat()] + board_ends),
        source_updated=None,  # boards carry no "last updated" stamp
        halls=results,
        warnings=warnings,
    )
