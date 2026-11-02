"""Danderyds kommun: danderyd.se "Ishallar och uterinkar".

Page layout (as of Nov 2026, Optimizely): plain rich text in ``div.mainbody``,
a flat run of ``<h2>``, ``<h3>`` and ``<p>``:

* ``<h2>Nu startar skridskosäsongen 2026/2027</h2>`` + "Vecka 40 drar
  skridskosäsongen igång igen i våra två ishallar. De håller sedan öppet till
  och med vecka 12." -> the indoor season (weeks without a year; the years come
  from "2026/2027").
* Optionally a dated block, e.g. ``<h2>Extra öppet under höstlovet vecka 44!</h2>``
  with ``<h3>Ishallen Enebybergs IP</h3>`` and lines
  "<strong>Måndag 26/10</strong> kl. 15.30-17.50 ingen klubba". These lines are
  hand-typed and contain typos ("15-30-17.50", "kl. 1300-14.50"). A listed date
  replaces the weekly times on that date for that hall; dates already past are
  skipped without reading.
* ``<h2>Ordinarie öppettider</h2>`` with ``<h3>Stockhagens IP - ishallen</h3>`` /
  ``<h3>Enebybergs IP - ishallen</h3>``, "Adress: …", "Öppettider:" and weekly
  lines "Onsdagar 10-12.50, med klubba <strong>(OBS! Inställt onsd 28/10)</strong>".
  Every line states its own stick rule ("med/utan/ingen klubba"); there is no default.
* ``<h3>Danderydsvallen - uterinken …</h3>`` (outdoor rinks) has no times; it is
  not emitted. If it ever lists times, we warn so that someone adds it.

"Uppdaterad: 2026-10-28" sits below the main body.
"""
from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup

from .common import (
    STICK_UNKNOWN, TIME_RANGE_RE, AdapterResult, BadTimeRange, HallResult, ParseError, Session,
    Slot, dm_date, expand_weekly, http_get, iso_week_monday, iso_week_sunday, norm_space,
    recurring_window, stick_from_text, time_ranges, week_year, weekday_number,
)

NAME = "danderyd-web"
MUNICIPALITY = "Danderyd"
SOURCE_URL = ("https://www.danderyd.se/uppleva-och-gora/idrott-motion-och-friluftsliv/idrott/"
              "ishallar-och-uterinkar/")

# keyword in the h3 -> (hall_id, name, rink)
HALLS = {
    "enebyberg": ("danderyd-enebybergs-ip-ishallen", "Enebybergs IP", "Ishallen"),
    "stockhag": ("danderyd-stockhagens-ip-ishallen", "Stockhagens IP", "Ishallen"),
}
IGNORED = ("danderydsvallen",)  # outdoor rinks, no times on the page

SEASON_YEARS_RE = re.compile(r"säsongen\s+(\d{4})\s*/\s*(\d{4})", re.I)
SEASON_RE = re.compile(r"vecka\s+(\d{1,2})\b.*?ishallar.*?till\s+och\s+med\s+vecka\s+(\d{1,2})", re.I | re.S)
DATED_LINE_RE = re.compile(r"^([a-zåäö]+)\s+(\d{1,2})/(\d{1,2})\b\s*(.*)$", re.I)
WEEKLY_LINE_RE = re.compile(r"^([a-zåäö]+)\s+(\d.*)$", re.I)
CANCEL_RE = re.compile(r"\(\s*OBS!?\s*Inställ\w*\s+(?:[a-zåäö]+\.?\s+)?(\d{1,2})/(\d{1,2})\s*\)", re.I)
UPDATED_RE = re.compile(r"Uppdaterad:?\s*(\d{4})-(\d{2})-(\d{2})")
CLOSED_RE = re.compile(r"stängt|stängd|inställ", re.I)


def fetch() -> dict[str, str]:
    return {"page": http_get(SOURCE_URL)}


def _hall_key(h3: str) -> str | None:
    low = h3.lower()
    for k in HALLS:
        if k in low:
            return k
    return None


def _times(text: str, where: str, warnings: list[str]) -> list[tuple[str, str, str, str | None]] | None:
    """'kl. 15.30-17.50 ingen klubba' -> [(start, end, stick, note)], None (+warning) if unreadable."""
    try:
        ranges = time_ranges(text)
    except BadTimeRange as exc:
        warnings.append(f"{where}: unreadable time in {text!r} ({exc})")
        return None
    rest = TIME_RANGE_RE.sub("", text)
    rest = re.sub(r"\bkl\b\.?", "", rest, flags=re.I)
    if not ranges:
        warnings.append(f"{where}: unreadable time in {text!r}")
        return None
    stick = stick_from_text(rest)
    leftover = re.sub(r"[\s,.;:–\-]+", " ", rest).strip()
    note = None if stick else (leftover or None)
    if stick is None and leftover:
        warnings.append(f"{where}: unknown wording {leftover!r}")
    return [(s, e, stick or STICK_UNKNOWN, note) for s, e in ranges]


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    soup = BeautifulSoup(raw["page"], "lxml")
    body = soup.select_one("div.mainbody")
    if body is None:
        raise ParseError("No div.mainbody – has the page layout changed?")

    warnings: list[str] = []
    season_years: tuple[int, int] | None = None
    season_weeks: tuple[int, int] | None = None
    section = None          # "intro" | "dated" | "ordinary" | "other"
    hall = None             # key in HALLS, "ignored", "unknown" or None
    slots: dict[str, list[Slot]] = {k: [] for k in HALLS}
    cancelled: dict[str, set[date]] = {k: set() for k in HALLS}
    dated: dict[str, dict[date, list[Session]]] = {k: {} for k in HALLS}
    seen_ordinary: set[str] = set()

    for el in body.find_all(["h2", "h3", "p", "li"]):
        text = norm_space(el.get_text(" "))
        if not text:
            continue
        if el.name == "h2":
            hall = None
            if m := SEASON_YEARS_RE.search(text):
                season_years = (int(m.group(1)), int(m.group(2)))
                section = "intro"
            elif re.search(r"ordinarie", text, re.I):
                section = "ordinary"
            elif re.search(r"\blov|vecka\s+\d", text, re.I):
                section = "dated"
            else:
                section = "other"
            continue
        if el.name == "h3":
            key = _hall_key(text)
            if key:
                hall = key
                if section == "ordinary":
                    seen_ordinary.add(key)
            elif any(i in text.lower() for i in IGNORED):
                hall = "ignored"
            else:
                hall = "unknown"
                if section in ("dated", "ordinary"):
                    warnings.append(f"Unknown facility heading {text!r}; not parsed")
            continue
        if section == "intro" and season_weeks is None and (m := SEASON_RE.search(text)):
            season_weeks = (int(m.group(1)), int(m.group(2)))
            continue
        if section not in ("dated", "ordinary") or hall is None:
            continue
        if hall in ("ignored", "unknown"):
            if re.search(r"\d{1,2}[.:]?\d{0,2}\s*[–\-]\s*\d", text) and not text.lower().startswith("adress"):
                warnings.append(f"Times for a facility that is not parsed: {text!r}")
            continue
        hid = HALLS[hall][0]
        if section == "dated":
            m = DATED_LINE_RE.match(text)
            if not m or weekday_number(m.group(1)) is None:
                if re.search(r"\d", text):
                    warnings.append(f"{hid}: unreadable line {text!r}")
                continue
            day = dm_date(m.group(2), m.group(3), today)
            if day < today:
                continue
            if day.weekday() != weekday_number(m.group(1)):
                warnings.append(f"{hid}: weekday does not match date in {text!r}")
                continue
            if CLOSED_RE.search(m.group(4)):
                dated[hall].setdefault(day, [])
                continue
            ts = _times(m.group(4), hid, warnings)
            dated[hall].setdefault(day, [])  # even if unreadable: the weekly time does not apply
            if ts is None:
                continue
            dated[hall][day].extend(
                Session(day.isoformat(), s, e, st, SOURCE_URL, note) for s, e, st, note in ts)
            continue
        # ordinary
        low = text.lower()
        if low.startswith("adress") or low.rstrip(": ") == "öppettider":
            continue
        line = text
        for c in CANCEL_RE.finditer(line):
            cancelled[hall].add(dm_date(c.group(1), c.group(2), today))
        line = CANCEL_RE.sub("", line).strip()
        m = WEEKLY_LINE_RE.match(line)
        wd = weekday_number(m.group(1)) if m else None
        if wd is None:
            warnings.append(f"{hid}: unreadable line {text!r}")
            continue
        if "(" in m.group(2):
            warnings.append(f"{hid}: unrecognised remark in {text!r}; time skipped")
            continue
        ts = _times(m.group(2), hid, warnings)
        for s, e, st, note in ts or []:
            slots[hall].append(Slot(wd, s, e, st, note))

    missing = set(HALLS) - seen_ordinary
    if missing:
        raise ParseError(f"Ordinary times missing for {sorted(missing)} – has the page layout changed?")
    if season_weeks is None:
        raise ParseError("Season (vecka N … till och med vecka M) not found")
    w_from, w_to = season_weeks
    if season_years:
        y_from, y_to = season_years
        if w_to >= w_from:
            y_to = y_from
    else:
        y_from = week_year(w_from, today)
        y_to = y_from + (1 if w_to < w_from else 0)
    s_from, s_to = iso_week_monday(y_from, w_from), iso_week_sunday(y_to, w_to)

    window = recurring_window(today, s_from, s_to)
    empty_reason = None
    if window is None:
        window = recurring_window(today)
        empty_reason = "The indoor season stated on the page is over or has not started"
    lo, hi = window

    halls: dict[str, HallResult] = {}
    for key, (hid, name, rink) in HALLS.items():
        sessions: list[Session] = []
        if empty_reason is None:
            sessions += expand_weekly(slots[key], lo, hi, SOURCE_URL,
                                      skip=cancelled[key] | set(dated[key]))
        for d, ss in dated[key].items():
            if lo <= d <= hi:
                sessions += ss
        out, seen = [], set()
        for s in sorted(sessions, key=lambda s: (s.date, s.start, s.end)):
            k = (s.date, s.start, s.end, s.stick)
            if k not in seen:
                seen.add(k)
                out.append(s)
        halls[hid] = HallResult(hall_id=hid, name=name, rink=rink, sessions=out)

    m = UPDATED_RE.search(soup.get_text(" "))
    updated = f"{m.group(1)}-{m.group(2)}-{m.group(3)}T00:00" if m else None
    return AdapterResult(covers_from=lo.isoformat(), covers_to=hi.isoformat(), source_updated=updated,
                         halls=halls, warnings=warnings, empty_reason=empty_reason)
