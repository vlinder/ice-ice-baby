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
from datetime import date, datetime
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


@dataclass
class AdapterResult:
    covers_from: str
    covers_to: str
    source_updated: str | None
    halls: dict[str, HallResult]
    warnings: list[str] = field(default_factory=list)


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
