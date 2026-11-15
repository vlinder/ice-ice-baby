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
  Every indoor line states its own stick rule ("med/utan/ingen klubba"); there is no
  default. A remark like "Obs! Ingen åkning i ishallen … helgen 14-15/11 på grund
  av …" cancels the weekly times on those dates.
* ``<h3>Danderydsvallen - uterinken och "Frimärket"</h3>`` (outdoor rink): in
  season it lists "Öppettider uterinken fr.om. 14/11 - med reservation för ändring
  på grund av vädret!" and weekly lines such as "Måndagar, tisdagar, torsdagar,
  fredagar: kl. 09.00-15.30" or "Fredagar även kvällsöppet kl. 19.00-20.30!".
  Those become a hall (stick unknown: the page says nothing about sticks), from the
  "fr.om." date on, with the weather reservation as the note. Out of season it has
  no times and is not emitted. "Frimärket" (a small rink) never has times.
* Other outdoor rinks (``<h3>Stockhagens IP - uterinken</h3>``) have no times, only
  prose ("beräknas kunna öppnas under vecka 49"); they are not emitted. If one ever
  lists times, we warn so that someone adds it.

Prose lines without a clock time (an opening date, "vecka 7-8") are ignored; a
cancellation that cannot be read is warned about.

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

# key -> (keywords that must all be in the h3, hall_id, name, rink)
HALL_DEFS = {
    "enebyberg": (("enebyberg", "ishall"), "danderyd-enebybergs-ip-ishallen", "Enebybergs IP", "Ishallen"),
    "stockhag": (("stockhag", "ishall"), "danderyd-stockhagens-ip-ishallen", "Stockhagens IP", "Ishallen"),
    "danderydsvallen": (("danderydsvallen", "uterink"), "danderyd-danderydsvallen-uterinken",
                        "Danderydsvallen", "Uterinken"),
}
HALLS = {k: v[1:] for k, v in HALL_DEFS.items()}  # key -> (hall_id, name, rink)
REQUIRED = ("enebyberg", "stockhag")  # the indoor halls always list ordinary times
IGNORED = ("uterink",)  # other outdoor rinks: no times on the page

SEASON_YEARS_RE = re.compile(r"säsongen\s+(\d{4})\s*/\s*(\d{4})", re.I)
SEASON_RE = re.compile(r"vecka\s+(\d{1,2})\b.*?ishallar.*?till\s+och\s+med\s+vecka\s+(\d{1,2})", re.I | re.S)
DATED_LINE_RE = re.compile(r"^([a-zåäö]+)\s+(\d{1,2})/(\d{1,2})\b\s*(.*)$", re.I)
# "Lördag 12-13.50, …", "Onsdagar kl. 12.00-20.00", "Måndagar, tisdagar, torsdagar, fredagar: kl. 09.00-15.30",
# "Fredagar även kvällsöppet kl. 19.00-20.30!"
WEEKLY_LINE_RE = re.compile(
    r"^([a-zåäö]+(?:\s*(?:,|och)\s*[a-zåäö]+)*)\s*(?:även(?:\s+kvällsöppet)?\s*)?:?\s*"
    r"((?:kl\.?\s*)?\d.*)$", re.I)
FROM_RE = re.compile(r"fr\.?\s*o\.?\s*m\.?\s*(\d{1,2})/(\d{1,2})", re.I)
RESERVATION_RE = re.compile(r"med\s+reservation\s+för\s+ändring[^!.]*", re.I)
NO_SKATING_RE = re.compile(r"\bingen\s+åkning\b|\binställ\w*|\bstängt\b|\bstängd\b", re.I)
DAY_SPAN_RE = re.compile(r"(?<![\d/])(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?/(\d{1,2})\b")
NOT_CLOCK_RE = re.compile(r"\bvecka\s+\d{1,2}(?:\s*[-–]\s*\d{1,2})?|(?<![\d/])\d{1,2}(?:\s*[-–]\s*\d{1,2})?/\d{1,2}\b"
                          r"|\b\d{4}\b", re.I)
CANCEL_RE = re.compile(r"\(\s*OBS!?\s*Inställ\w*\s+(?:[a-zåäö]+\.?\s+)?(\d{1,2})/(\d{1,2})\s*\)", re.I)
UPDATED_RE = re.compile(r"Uppdaterad:?\s*(\d{4})-(\d{2})-(\d{2})")
CLOSED_RE = re.compile(r"stängt|stängd|inställ", re.I)


def fetch() -> dict[str, str]:
    return {"page": http_get(SOURCE_URL)}


def _hall_key(h3: str) -> str | None:
    low = h3.lower()
    for k, (words, *_) in HALL_DEFS.items():
        if all(w in low for w in words):
            return k
    return None


TIMEISH_RE = re.compile(r"\d{1,2}[.:]?\d{0,2}\s*[–\-]\s*\d")


def _has_clock_time(text: str) -> bool:
    """Whether text holds something that looks like a time range (typos included),
    once week numbers ("vecka 7-8"), dates ("14-15/11") and years are taken out."""
    return bool(TIMEISH_RE.search(NOT_CLOCK_RE.sub(" ", text)))


def _no_skating_dates(text: str, today: date) -> list[date] | None:
    """'… Ingen åkning … helgen 14-15/11 …' -> the dates; None if it names none."""
    out = []
    for m in DAY_SPAN_RE.finditer(text):
        first, last, month = int(m.group(1)), int(m.group(2) or m.group(1)), m.group(3)
        if last < first or last - first > 6:
            return None
        out += [dm_date(d, month, today) for d in range(first, last + 1)]
    return out or None


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
    leftover = re.sub(r"[\s,.;:!–\-]+", " ", rest).strip()
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
    starts: dict[str, date] = {}         # "Öppettider uterinken fr.om. 14/11"
    notes: dict[str, str] = {}           # "med reservation för ändring på grund av vädret"

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
            if _has_clock_time(text) and not text.lower().startswith("adress"):
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
        if low.startswith("öppettider") and not _has_clock_time(text):
            if m := FROM_RE.search(text):
                starts[hall] = dm_date(m.group(1), m.group(2), today)
            if m := RESERVATION_RE.search(text):
                notes[hall] = norm_space(m.group(0))
            if not (FROM_RE.search(text) or RESERVATION_RE.search(text)):
                warnings.append(f"{hid}: unreadable line {text!r}")
            continue
        if NO_SKATING_RE.search(text) and not WEEKLY_LINE_RE.match(text):
            days = _no_skating_dates(text, today)
            if days is None:
                warnings.append(f"{hid}: unreadable line {text!r}")
            else:
                cancelled[hall].update(days)
            continue
        first = re.match(r"[a-zåäö]+", low)
        if not _has_clock_time(text) and not (first and weekday_number(first.group(0)) is not None):
            continue  # prose: "Isrinken … beräknas kunna öppnas under vecka 49", an opening date
        line = text
        for c in CANCEL_RE.finditer(line):
            cancelled[hall].add(dm_date(c.group(1), c.group(2), today))
        line = CANCEL_RE.sub("", line).strip()
        m = WEEKLY_LINE_RE.match(line)
        wds = [weekday_number(w) for w in re.split(r"\s*(?:,|\boch\b)\s*", m.group(1))] if m else [None]
        if None in wds:
            warnings.append(f"{hid}: unreadable line {text!r}")
            continue
        if "(" in m.group(2):
            warnings.append(f"{hid}: unrecognised remark in {text!r}; time skipped")
            continue
        ts = _times(m.group(2), hid, warnings)
        for wd in wds:
            for s, e, st, note in ts or []:
                slots[hall].append(Slot(wd, s, e, st, note or notes.get(hall)))

    missing = set(REQUIRED) - seen_ordinary
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
        if key not in REQUIRED and not slots[key] and not dated[key]:
            continue  # outdoor rink out of season: no times on the page
        sessions: list[Session] = []
        if empty_reason is None:
            start = max(lo, starts[key]) if key in starts else lo
            sessions += expand_weekly(slots[key], start, hi, SOURCE_URL,
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
