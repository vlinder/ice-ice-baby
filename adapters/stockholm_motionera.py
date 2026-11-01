"""Stockholms stad: motionera.stockholm/skridskoakning

Page layout (as of Nov 2026): an accordion with one item per week, titled
"26 oktober–1 november". Inside each item:

    <h3>Farsta idrottsplats</h3>        facility
    <h4>Ishallen</h4>                    rink
    <ul><li>Måndag: 10.00–12.30, delad is</li> ... </ul>

Times changed after first publication are wrapped in <strong>.
Facilities without any public times that week are usually left out entirely,
so a known hall missing from a covered week means "no public skating".
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from bs4 import BeautifulSoup, Tag

from .common import (
    STICK_SHARED, STICK_UNKNOWN, STICK_WITH, STICK_WITHOUT,
    AdapterResult, HallResult, ParseError, Session,
    hhmm, http_get, infer_year, month_number, norm_space, slugify,
)

NAME = "stockholm-motionera"
MUNICIPALITY = "Stockholm"
SOURCE_URL = "https://motionera.stockholm/skridskoakning/"

WEEKDAYS = {"måndag": 0, "tisdag": 1, "onsdag": 2, "torsdag": 3, "fredag": 4, "lördag": 5, "söndag": 6}

WEEK_RE = re.compile(
    r"(\d{1,2})(?:\s+([a-zåäö]+))?(?:\s+(\d{4}))?\s*[–\-]\s*(\d{1,2})\s+([a-zåäö]+)(?:\s+(\d{4}))?",
    re.I,
)
TIME_RE = re.compile(
    r"(\d{1,2})[.:](\d{2})\s*[–\-]\s*(\d{1,2})[.:](\d{2})\s*,?\s*(delad is|med klubba|utan klubba)?",
    re.I,
)
UPDATED_RE = re.compile(r"uppdaterad\s+(\d{1,2})\s+([a-zåäö]+)(?:\s+(\d{4}))?\s+klockan\s+(\d{1,2})[.:](\d{2})", re.I)
NONE_RE = re.compile(r"inga\s+(skridsko)?tider|stängt|ingen is", re.I)

STICK_MAP = {"delad is": STICK_SHARED, "med klubba": STICK_WITH, "utan klubba": STICK_WITHOUT}


def fetch() -> dict[str, str]:
    return {"page": http_get(SOURCE_URL)}


def hall_id(facility: str, rink: str | None) -> str:
    return "stockholm-" + slugify(facility) + (("-" + slugify(rink)) if rink else "")


def _parse_week_title(title: str, today: date) -> tuple[date, date]:
    m = WEEK_RE.search(title)
    if not m:
        raise ParseError(f"Unrecognised week title: {title!r}")
    d1, m1, y1, d2, m2, y2 = m.groups()
    m1 = m1 or m2  # "2–8 november"
    mo1, mo2 = month_number(m1), month_number(m2)
    if y1:
        year1 = int(y1)
    elif y2:
        year1 = int(y2) - (1 if mo2 < mo1 else 0)
    else:
        year1 = infer_year(mo1, int(d1), today)
    year2 = int(y2) if y2 else (year1 + 1 if mo2 < mo1 else year1)
    start, end = date(year1, mo1, int(d1)), date(year2, mo2, int(d2))
    if start.weekday() != 0 or (end - start).days != 6:
        raise ParseError(f"Week title is not Monday–Sunday: {title!r} -> {start}..{end}")
    return start, end


def _parse_updated(text: str, today: date) -> str | None:
    m = UPDATED_RE.search(text)
    if not m:
        return None
    d, mo, y, hh, mm = m.groups()
    mon = month_number(mo)
    year = int(y) if y else infer_year(mon, int(d), today)
    return f"{year:04d}-{mon:02d}-{int(d):02d}T{hhmm(hh, mm)}"


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    soup = BeautifulSoup(raw["page"], "lxml")
    headers = soup.select("button.accordion-item__header")
    weeks: list[tuple[date, date, Tag]] = []
    for btn in headers:
        title_el = btn.select_one(".accordion-item__title")
        body = soup.find(id=btn.get("aria-controls", ""))
        if not title_el or not body:
            continue
        title = norm_space(title_el.get_text())
        if not WEEK_RE.search(title):
            continue  # some other accordion on the page
        start, end = _parse_week_title(title, today)
        weeks.append((start, end, body))

    if not weeks:
        raise ParseError("No week sections found – has the page layout changed?")

    halls: dict[str, HallResult] = {}
    warnings: list[str] = []
    updated: list[str] = []

    for start, end, body in weeks:
        u = _parse_updated(body.get_text(" "), today)
        if u:
            updated.append(u)
        facility: str | None = None
        rink: str | None = None
        for el in body.find_all(["h3", "h4", "ul"]):
            if el.name == "h3":
                facility, rink = norm_space(el.get_text()), None
                continue
            if el.name == "h4":
                rink = norm_space(el.get_text())
                continue
            if facility is None:
                continue  # the explanatory "delad is" list before the first facility
            hid = hall_id(facility, rink)
            hall = halls.setdefault(hid, HallResult(hall_id=hid, name=facility, rink=rink))
            for li in el.find_all("li", recursive=False):
                line = norm_space(li.get_text(" "))
                if ":" not in line:
                    warnings.append(f"{hid}: unexpected line {line!r}")
                    continue
                day_word, rest = line.split(":", 1)
                wd = WEEKDAYS.get(day_word.strip().lower())
                if wd is None:
                    warnings.append(f"{hid}: unknown weekday in {line!r}")
                    continue
                day = start + timedelta(days=wd)
                matches = list(TIME_RE.finditer(rest))
                if not matches:
                    if not NONE_RE.search(rest):
                        warnings.append(f"{hid}: could not read {line!r}")
                    continue
                changed = li.find("strong") is not None
                for m in matches:
                    h1, m1, h2, m2, kind = m.groups()
                    s, e = hhmm(h1, m1), hhmm(h2, m2)
                    if e <= s:
                        raise ParseError(f"{hid}: end before start in {line!r}")
                    hall.sessions.append(Session(
                        date=day.isoformat(), start=s, end=e,
                        stick=STICK_MAP.get((kind or "").lower(), STICK_UNKNOWN),
                        source_url=SOURCE_URL, changed=changed,
                    ))

    return AdapterResult(
        covers_from=min(w[0] for w in weeks).isoformat(),
        covers_to=max(w[1] for w in weeks).isoformat(),
        source_updated=max(updated) if updated else None,
        halls=halls,
        warnings=warnings,
    )
