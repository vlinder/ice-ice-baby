"""Shared helpers for adapters.

An adapter is a module with:
    NAME: str                       unique adapter id, e.g. "stockholm-motionera"
    SOURCE_URL: str                 human-readable page that people can verify against
    def fetch() -> dict[str, str]   download raw source documents (network)
    def parse(raw, today) -> AdapterResult   pure function, no network

Keeping fetch and parse separate lets tests run parse() against saved fixtures.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("Europe/Stockholm")
USER_AGENT = "allmanhetens-akning-bot/1.0 (+https://github.com/; schedule aggregator)"

STICK_SHARED = "shared"
STICK_WITH = "with_stick"
STICK_WITHOUT = "no_stick"
STICK_UNKNOWN = "unknown"


class ParseError(Exception):
    """The source was fetched but did not look like we expect. Fail loudly."""


@dataclass
class Session:
    date: str            # YYYY-MM-DD, local Stockholm date
    start: str           # HH:MM, local wall-clock time
    end: str             # HH:MM
    stick: str           # one of STICK_*
    source_url: str
    note: str | None = None
    changed: bool = False  # source marks this time as changed since first publication

    def to_json(self) -> dict:
        d = {
            "date": self.date,
            "start": self.start,
            "end": self.end,
            "stick": self.stick,
            "source_url": self.source_url,
            "note": self.note,
        }
        if self.changed:
            d["changed"] = True
        return d


@dataclass
class HallResult:
    hall_id: str
    name: str            # name as seen in the source (curated name lives in config/halls.json)
    rink: str | None
    sessions: list[Session] = field(default_factory=list)
    # Per-hall covered range, for sources where halls are listed over different
    # horizons (e.g. one Rbok board per hall). None = use the adapter-wide range.
    covers_from: str | None = None
    covers_to: str | None = None


@dataclass
class AdapterResult:
    covers_from: str
    covers_to: str
    source_updated: str | None
    halls: dict[str, HallResult]
    warnings: list[str] = field(default_factory=list)
    # Set when the source itself says there are no public times right now
    # ("No records to display", "closed for the season"). It stops the
    # zero-sessions check from sending every run to review.
    empty_reason: str | None = None


def now_local() -> datetime:
    return datetime.now(TZ).replace(microsecond=0)


def today_local() -> date:
    return now_local().date()


def http_get(url: str, *, params: dict | None = None, timeout: int = 30) -> str:
    r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    r.encoding = r.encoding or "utf-8"
    return r.text


def slugify(text: str) -> str:
    text = text.replace("å", "a").replace("ä", "a").replace("ö", "o")
    text = text.replace("Å", "a").replace("Ä", "a").replace("Ö", "o")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def hhmm(h: str | int, m: str | int) -> str:
    h, m = int(h), int(m)
    if not (0 <= h <= 24 and 0 <= m < 60):
        raise ParseError(f"Bad time {h}:{m}")
    return f"{h:02d}:{m:02d}"


SV_MONTHS = {
    "januari": 1, "februari": 2, "mars": 3, "april": 4, "maj": 5, "juni": 6,
    "juli": 7, "augusti": 8, "september": 9, "oktober": 10, "november": 11, "december": 12,
    # three-letter forms, Swedish and English
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "okt": 10, "oct": 10, "nov": 11, "dec": 12,
}


def month_number(word: str) -> int:
    w = word.lower().strip(".")
    if w in SV_MONTHS:
        return SV_MONTHS[w]
    if w[:3] in SV_MONTHS:
        return SV_MONTHS[w[:3]]
    raise ParseError(f"Unknown month: {word!r}")


def infer_year(month: int, day: int, today: date) -> int:
    """Sources omit the year. Pick the year that puts the date closest to today."""
    best = None
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        dist = abs((d - today).days)
        if best is None or dist < best[0]:
            best = (dist, y)
    if best is None:
        raise ParseError(f"Invalid date {day}/{month}")
    return best[1]


# ---------------------------------------------------------------------------
# Helpers for hand-written municipal pages
# ---------------------------------------------------------------------------

# How far ahead a recurring weekly schedule ("Måndagar 14.00–15.45") is expanded.
# Pages rarely announce changes far in advance, so we only claim four weeks.
RECURRING_HORIZON_DAYS = 28

_WEEKDAY_FULL = ["måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag"]
_WEEKDAY_SHORT = {"mån": 0, "tis": 1, "ons": 2, "tor": 3, "tors": 3, "fre": 4, "lör": 5, "sön": 6}


def weekday_number(word: str) -> int | None:
    """'Måndag', 'måndagar', 'Måndagen', 'mån', 'Mån.' -> 0. None if not a weekday."""
    w = word.lower().strip(" .:,")
    for i, full in enumerate(_WEEKDAY_FULL):
        if w == full or (w.startswith(full) and w[len(full):] in ("ar", "en", "s")):
            return i
    return _WEEKDAY_SHORT.get(w)


# A time range: "14.00-15.45", "14:00 – 15:45", "9-10.50", "kl. 1300-14.50", "12-14".
# The look-arounds reject runs like "09.00-14-00", "13.300", "26/10-1/11" and
# "26-30 oktober" instead of half-reading them. Still, only run it on text that
# is meant to hold times: a bare "2-8" could be anything.
TIME_RANGE_RE = re.compile(
    r"(?<![\d.:/\-–])(\d{1,2})(?:[.:]?(\d{2}))?\s*[–—-]\s*(\d{1,2})(?:[.:]?(\d{2}))?"
    r"(?![.:/\-–]?\d)(?!\s*(?:jan|feb|mar|apr|maj|jun|jul|aug|sep|okt|nov|dec))",
    re.I,
)


class BadTimeRange(ValueError):
    """Something that looks like a time range but is not a valid one."""


def time_ranges(text: str) -> list[tuple[str, str]]:
    """All valid HH:MM ranges in text. Raises BadTimeRange on an impossible one
    (e.g. a typo like "15-30-17.50"); callers turn that into a warning."""
    out = []
    for m in TIME_RANGE_RE.finditer(text):
        h1, m1, h2, m2 = m.groups()
        try:
            s, e = hhmm(h1, m1 or 0), hhmm(h2, m2 or 0)
        except ParseError as exc:
            raise BadTimeRange(f"{m.group(0)!r}: {exc}") from None
        if e <= s:
            raise BadTimeRange(f"{m.group(0)!r}: end is not after start")
        out.append((s, e))
    return out


_SHARED_RE = re.compile(r"\bhalva\b|\bdelad\b|uppdelad|halv plan|halva plan", re.I)
_WITHOUT_RE = re.compile(
    r"\b(utan|ingen|inga|ej)\b[^.;()]{0,25}\b(klubb|puck|boll)"
    r"|\b(klubb|puck)\w*[^.;()]{0,20}\b(ej|inte|ejtillåt)\s+tillåt"
    r"|inte\s+tillåtet\s+med\s+(klubb|puck)"
    r"|(klubb|puck)\w*förbud",
    re.I,
)
_WITH_RE = re.compile(r"\bmed\b[^.;()]{0,15}\b(klubb|puck)|\b(klubb|puck)\w*\s+tillåt", re.I)


def stick_from_text(text: str) -> str | None:
    """Map Swedish wording to a STICK_* value, or None if the text says nothing about sticks.

    "halva med klubba, halva utan" -> shared; "utan klubba", "ingen klubba",
    "klubba ej tillåten", "inte tillåtet med klubba" -> no_stick;
    "med klubba", "med puck & klubba", "klubba tillåten" -> with_stick.
    """
    if _SHARED_RE.search(text):
        return STICK_SHARED
    if _WITHOUT_RE.search(text):
        return STICK_WITHOUT
    if _WITH_RE.search(text):
        return STICK_WITH
    return None


def iso_week_monday(year: int, week: int) -> date:
    return date.fromisocalendar(year, week, 1)


def iso_week_sunday(year: int, week: int) -> date:
    return date.fromisocalendar(year, week, 7)


def week_year(week: int, today: date) -> int:
    """Pages say "vecka 45" without a year. Pick the year that puts the week closest to today."""
    best = None
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            d = iso_week_monday(y, week)
        except ValueError:
            continue
        dist = abs((d - today).days)
        if best is None or dist < best[0]:
            best = (dist, y)
    if best is None:
        raise ParseError(f"Invalid week {week}")
    return best[1]


def recurring_window(today: date, season_from: date | None = None,
                     season_to: date | None = None) -> tuple[date, date] | None:
    """The date range a recurring weekly schedule is expanded over: from today
    (or the season start) for RECURRING_HORIZON_DAYS, cut at the season end.
    None if the season is over or has not started within the horizon."""
    lo = max(today, season_from) if season_from else today
    hi = today + timedelta(days=RECURRING_HORIZON_DAYS - 1)
    if season_to:
        hi = min(hi, season_to)
    return (lo, hi) if lo <= hi else None


@dataclass
class Slot:
    """One recurring time in a weekly template."""
    weekday: int         # 0 = Monday
    start: str
    end: str
    stick: str
    note: str | None = None


def expand_weekly(slots: list[Slot], start: date, end: date, source_url: str,
                  skip: set[date] | None = None) -> list[Session]:
    """Turn a weekly template into dated sessions between start and end (inclusive)."""
    skip = skip or set()
    out = []
    d = start
    while d <= end:
        if d not in skip:
            for s in slots:
                if s.weekday == d.weekday():
                    out.append(Session(date=d.isoformat(), start=s.start, end=s.end,
                                       stick=s.stick, source_url=source_url, note=s.note))
        d += timedelta(days=1)
    return out


def sv_date(day: int | str, month_word: str, today: date, year: int | str | None = None) -> date:
    """'2', 'november' (+ optional year) -> date, inferring the year when missing."""
    mon = month_number(month_word)
    y = int(year) if year else infer_year(mon, int(day), today)
    try:
        return date(y, mon, int(day))
    except ValueError:
        raise ParseError(f"Invalid date {day} {month_word} {y}") from None


def dm_date(day: int | str, month: int | str, today: date) -> date:
    """'2/11' style day/month -> date, inferring the year."""
    d, m = int(day), int(month)
    try:
        return date(infer_year(m, d, today), m, d)
    except ValueError:
        raise ParseError(f"Invalid date {d}/{m}") from None


def page_text(html: str, root_selector: str | None = None) -> list[str]:
    """Visible text of a page as a list of non-empty, whitespace-normalised lines.

    Block elements become line breaks; scripts, styles, nav, header and footer are dropped.
    """
    from bs4 import BeautifulSoup  # local import keeps common.py light for non-HTML adapters

    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "svg", "nav", "header", "footer", "template"]):
        t.decompose()
    root = soup.select_one(root_selector) if root_selector else (soup.find("main") or soup.body or soup)
    if root is None:
        raise ParseError(f"Page has no element matching {root_selector!r}")
    for br in root.find_all("br"):
        br.replace_with("\n")
    lines = [norm_space(x) for x in root.get_text("\n").split("\n")]
    return [x for x in lines if x]
