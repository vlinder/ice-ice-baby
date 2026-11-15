"""Shared reader for FRI-GO's public schedule (`/bokning/schema.aspx`).

Some municipalities publish no FRI-GO list URL (see frigo.py: those carry a
checksum and cannot be made up), but their booking system's public schedule is
open to anyone: pick one or more facilities on `default.aspx`, choose a period
and press "Sök". The search is an ASP.NET postback, so `fetch_schedules` first
GETs `default.aspx` for its form state, then posts it with the facility
(`chkListaObjekt`) and the period (`txtFdat`/`txtTdat`). One facility per request.

Markup (Nov 2026), one table for the whole period, one column per day:

    <span>Schedule 2026-11-16 - 2026-12-13</span>          ("Schema …" in Swedish)
    <table class="schemagrafiskt">
      <tr><th>v.47</th><th>Mån&nbsp;16/11</th><th>Tis&nbsp;17/11</th>…</tr>
      <tr><td>6</td><td><div class="wrapper">…cells…</div></td>…</tr>   one row per hour

Cells inside a day's column:

    <div class="bokning" id="cphHuvud_div_42_20261121_226235"
         onmouseover="Tip('<div …>Time: …<br />Activity: Allmänheten<br />
                           Märkning (…): Utan klubba<br />Click for more information</div>', …)"
         onclick="return visaInfo('bokningvisaajax.aspx?boknr=226235&amp;objekt=42&amp;aktdat=20261121…')">
      <span>Allmänheten<br />Utan klubba<br />11:00-13:00</span></div>
    <div class="ledigt">…</div>      free time
    <div class="stangt">…</div>      closed
    <div class="sparrat"><span>Blocked for<br />viewing</span></div>
                                     the system hides this day from the public

The id holds the facility, the date and the booking number. The booking's own
time is the last line of its <span>; the tooltip carries the activity and the
booker's label (both free text set by the municipality). The tooltip's "Time:"
line is garbled by FRI-GO itself ("1689193:55-11:00") and is not used.

Rules:
- A booking is public skating when the municipality says so in its activity or
  label: the word "allmänhet" ("Allmänheten", "Allmänhet Utan klubbor").
  "Förskola allmäntid" is not. Municipalities may pass their own test.
- Stick from the same text; without wording the stick is unknown and the label
  is kept as the note.
- `covers` is the period the schedule shows, from today, cut before the first
  day the system blocks from viewing.
- Each session links to the booking's own information page.
- No schedule heading or table, a booking id for another facility or another
  date than its column, or a day column that can't be read -> ParseError.
  A booking without a readable time is skipped with a warning.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

import requests
from bs4 import BeautifulSoup

from .common import (
    STICK_UNKNOWN, USER_AGENT, AdapterResult, HallResult, ParseError, Session,
    hhmm, norm_space, stick_from_text, tags_from_text,
)

# How far ahead to ask for. The schedules hold whole seasons, but some
# municipalities block days further ahead from viewing (see `covers`).
HORIZON_DAYS = 42

PERIOD_RE = re.compile(r"\b(?:Schedule|Schema)\s+(\d{4})-(\d{2})-(\d{2})\s*-\s*(\d{4})-(\d{2})-(\d{2})")
DAY_HEAD_RE = re.compile(r"^\S+\s+(\d{1,2})/(\d{1,2})\b")
CELL_ID_RE = re.compile(r"^cphHuvud_div_(\d+)_(\d{4})(\d{2})(\d{2})_(\d+)$")
TIP_RE = re.compile(r"Tip\('(.*?)',\s*BORDERWIDTH", re.S)
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")
PUBLIC_RE = re.compile(r"allmänhet", re.I)
# Tooltip lines that are not about who or what: the (garbled) time and the hint.
_SKIP_TIP_RE = re.compile(r"^(time|tid|click|klicka)\b", re.I)


@dataclass(frozen=True)
class Rink:
    obj: int             # FRI-GO facility id (value of chkListaObjekt)
    hall_id: str
    name: str
    rink: str | None = None


def is_public(info: str) -> bool:
    return bool(PUBLIC_RE.search(info))


def _key(rink: Rink) -> str:
    return str(rink.obj)


def fetch_schedules(base: str, rinks: list[Rink], today: date, days: int = HORIZON_DAYS) -> dict[str, str]:
    """base is the booking root, e.g. "https://jarfalla.fri-go.se/bokning/"."""
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    out = {}
    for rink in rinks:
        r = s.get(base + "default.aspx", timeout=60)
        r.raise_for_status()
        form = BeautifulSoup(r.text, "lxml").find("form")
        if form is None:
            raise ParseError("FRI-GO start page has no form – has the layout changed?")
        data = {}
        for i in form.find_all("input"):
            name, kind = i.get("name"), (i.get("type") or "text").lower()
            if name and kind not in ("image", "submit", "checkbox", "radio", "button"):
                data[name] = i.get("value", "")
        if not any(k.endswith("txtFdat") for k in data):
            raise ParseError("FRI-GO start page has no date fields – has the layout changed?")
        data["chkListaObjekt"] = str(rink.obj)
        data["ctl00$cphHuvud$txtFdat"] = today.isoformat()
        data["ctl00$cphHuvud$txtTdat"] = (today + timedelta(days=days - 1)).isoformat()
        data["ctl00$cphHuvud$btnHdnSok"] = ""
        r = s.post(base + "schema.aspx", data=data, timeout=90)
        r.raise_for_status()
        r.encoding = r.encoding or "utf-8"
        out[_key(rink)] = r.text
    return out


def _tooltip_info(div) -> str:
    m = TIP_RE.search(div.get("onmouseover") or div.get("onfocus") or "")
    if not m:
        return ""
    text = BeautifulSoup(m.group(1).replace("\\'", "'"), "lxml").get_text("\n")
    values = []
    for line in (norm_space(x) for x in text.split("\n")):
        if not line or _SKIP_TIP_RE.match(line):
            continue
        values.append(line.split(": ", 1)[1].strip() if ": " in line else line)
    return ", ".join(v for v in values if v)


def _columns(table, period: tuple[date, date]) -> list[date | None]:
    """One entry per <th>: the day's date, or None for the week label."""
    out: list[date | None] = []
    for th in table.find_all("th"):
        text = norm_space(th.get_text(" "))
        m = DAY_HEAD_RE.match(text)
        if not m:
            out.append(None)
            continue
        d, mo = int(m[1]), int(m[2])
        for y in (period[0].year, period[1].year):
            try:
                day = date(y, mo, d)
            except ValueError:
                continue
            if period[0] <= day <= period[1]:
                out.append(day)
                break
        else:
            raise ParseError(f"Day heading {text!r} is outside the period {period[0]}..{period[1]}")
    days = [d for d in out if d]
    expected = (period[1] - period[0]).days + 1
    if len(days) != expected or days != sorted(days):
        raise ParseError(f"Schedule shows {len(days)} day columns for a {expected}-day period")
    return out


def parse_schedules(raw: dict[str, str], today: date, *, rinks: list[Rink], base: str,
                    public: Callable[[str], bool] = is_public) -> AdapterResult:
    """raw maps facility id (as text) -> schema.aspx HTML for that facility."""
    halls: dict[str, HallResult] = {}
    warnings: list[str] = []
    ranges: list[tuple[str, str]] = []
    for rink in rinks:
        html = raw.get(_key(rink))
        if html is None:
            raise ParseError(f"No schedule fetched for {rink.hall_id}")
        soup = BeautifulSoup(html, "lxml")
        pm = PERIOD_RE.search(soup.get_text(" "))
        table = soup.select_one("table.schemagrafiskt")
        if not pm or table is None:
            raise ParseError(f"{rink.hall_id}: no schedule on the page – has FRI-GO changed its layout?")
        period = (date(int(pm[1]), int(pm[2]), int(pm[3])), date(int(pm[4]), int(pm[5]), int(pm[6])))
        cols = _columns(table, period)

        hall = HallResult(hall_id=rink.hall_id, name=rink.name, rink=rink.rink)
        halls[rink.hall_id] = hall
        blocked: list[date] = []
        seen: set[tuple] = set()
        cells = 0
        for tr in table.find_all("tr"):
            for i, td in enumerate(tr.find_all("td", recursive=False)):
                day = cols[i] if i < len(cols) else None
                if day is None:
                    continue
                for div in td.select("div.sparrat"):
                    blocked.append(day)
                for div in td.select("div.ledigt, div.stangt"):
                    cells += 1
                for div in td.select("div.bokning"):
                    cells += 1
                    m = CELL_ID_RE.match(div.get("id", ""))
                    if not m:
                        raise ParseError(f"{rink.hall_id}: booking without a readable id {div.get('id')!r}")
                    obj, y, mo, d, booking = m.groups()
                    if int(obj) != rink.obj or date(int(y), int(mo), int(d)) != day:
                        raise ParseError(f"{rink.hall_id}: booking {div.get('id')!r} is not in its column ({day})")
                    span = div.find("span")
                    lines = [norm_space(x) for x in (span.get_text("\n") if span else "").split("\n")]
                    lines = [x for x in lines if x]
                    info = _tooltip_info(div) or ", ".join(lines[:-1])
                    if not public(info):
                        continue
                    tm = TIME_RE.match(lines[-1]) if lines else None
                    if not tm:
                        warnings.append(f"{rink.hall_id}: {day}: unreadable time in booking {' / '.join(lines)!r}")
                        continue
                    s, e = hhmm(tm[1], tm[2]), hhmm(tm[3], tm[4])
                    if e <= s:
                        raise ParseError(f"{rink.hall_id}: end before start in booking {' / '.join(lines)!r}")
                    if (day, s, e) in seen:
                        continue
                    seen.add((day, s, e))
                    stick = stick_from_text(info)
                    url = (f"{base}bokningvisaajax.aspx?boknr={booking}&objekt={obj}"
                           f"&aktdat={y}{mo}{d}&page=schema.aspx")
                    hall.sessions.append(Session(
                        date=day.isoformat(), start=s, end=e, stick=stick or STICK_UNKNOWN, source_url=url,
                        note=None if stick else (info or None), tags=tags_from_text(info)))
        if cells == 0 and not blocked:
            raise ParseError(f"{rink.hall_id}: the schedule has no cells – has FRI-GO changed its layout?")
        start = max(today, period[0])
        end = min(blocked) - timedelta(days=1) if blocked else period[1]
        if end < start:
            raise ParseError(f"{rink.hall_id}: the schedule is blocked from viewing from {min(blocked)}")
        hall.covers_from, hall.covers_to = start.isoformat(), end.isoformat()
        hall.sessions = [x for x in hall.sessions if start.isoformat() <= x.date <= hall.covers_to]
        hall.sessions.sort(key=lambda x: (x.date, x.start, x.end))
        ranges.append((hall.covers_from, hall.covers_to))

    return AdapterResult(
        covers_from=min(r[0] for r in ranges),
        covers_to=max(r[1] for r in ranges),
        source_updated=None,
        halls=halls,
        warnings=warnings,
    )
