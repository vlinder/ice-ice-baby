"""Salem: IFK Salem's SportAdmin page for public skating in Salems ishall

Salems kommun does not publish the times itself; it links to this club page.

Page layout (as of Nov 2026): a SportAdmin "sida" with one paragraph article,
titled "Allmänhetensåkning" (sic, no space), whose body is

    <ul>
      <li>28/10 08.00-08.50</li>
      <li>31/10 09.30-12.20 och 18.00-19.50</li>
      <li>6/11 13.00-16.50</li> ...
    </ul>
    <p><strong>Delad is (halva ytan med klubba+puck och andra halvan UTAN klubba + puck)</strong></p>
    <p><strong>Datum/tider uppdateras löpande.</strong></p>

The page never names the hall; it is always Salems ishall (the club's home rink).
Dates are day/month without a year. The list is open-ended ("uppdateras
löpande"), so `covers` runs from today to the last listed date. Past dates are
dropped. A dated line with "inställd"/"stängt" and no time means no skating
that day. If the list is gone and the page says "inga tider" (or similar), the
hall is emitted with no sessions and `empty_reason`; covers = recurring_window.

Stick: the page-wide "Delad is (halva ytan med klubba ...)" sentence is the
default for every listed time; a date line with its own wording overrides it.
Without any wording the stick is unknown. The page shows no "last updated" date.
"""
from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup, Tag

from .common import (
    STICK_UNKNOWN, AdapterResult, BadTimeRange, HallResult, ParseError, Session,
    dm_date, http_get, norm_space, recurring_window, stick_from_text, time_ranges,
)

NAME = "salem-sportadmin"
MUNICIPALITY = "Salem"
SOURCE_URL = "https://ifksalem.web.sportadmin.se/sida/?ID=623188"

HALL_NAME = "Salems ishall"
HALL_ID = "salem-salems-ishall"

TITLE_RE = re.compile(r"allmänhetens\s*(skridsko)?åkning", re.I)
# "28/10 08.00-08.50", "31/10: 09.30-12.20 och 18.00-19.50", "6/11 inställd"
DATE_LINE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})(?![\d/])\s*[:,.–-]?\s*(.*)$")
CLOSED_RE = re.compile(r"inställ|stängt|stängd|ingen\s+(allmän|åkning|skridsko)", re.I)
# Something that may be a time or a date in a line we do not otherwise understand.
TIMEISH_RE = re.compile(r"\d{1,2}[.:]\d{2}|\d{1,2}/\d{1,2}")
# The page explicitly says there are no times now.
NO_TIMES_RE = re.compile(r"inga\s+(fler\s+|nya\s+|planerade\s+)?(tider|åkningar)|ingen\s+allmänhetens", re.I)


def fetch() -> dict[str, str]:
    return {"page": http_get(SOURCE_URL)}


def _lines(el: Tag) -> list[str]:
    """Text of each <li>/<p> (or bare text block) in the body, one per line."""
    for br in el.find_all("br"):
        br.replace_with("\n")
    out = []
    for block in el.find_all(["li", "p"]):
        if block.find(["li", "p"]):
            continue  # container; its children are visited themselves
        for part in block.get_text("").split("\n"):
            t = norm_space(part)
            if t:
                out.append(t)
    # Text outside <li>/<p> (e.g. a bare <div> line) is returned too, one per line.
    rest = BeautifulSoup(str(el), "lxml")
    for block in rest.find_all(["li", "p"]):
        block.decompose()
    out += [norm_space(x) for x in rest.get_text("\n").split("\n") if norm_space(x)]
    return out


def _find_body(soup: BeautifulSoup) -> Tag:
    for art in soup.select("article.sa-paragraph"):
        title = art.select_one(".sa-paragraph__title")
        if title and TITLE_RE.search(norm_space(title.get_text())):
            body = art.select_one(".sa-paragraph__body")
            if body is None:
                raise ParseError("Public-skating article has no body")
            return body
    raise ParseError("No 'Allmänhetens åkning' article found – has the page layout changed?")


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    soup = BeautifulSoup(raw["page"], "lxml")
    lines = _lines(_find_body(soup))

    warnings: list[str] = []
    dated: list[tuple[date, str, str]] = []  # (date, rest of line, full line)
    default_stick: str | None = None
    for line in lines:
        m = DATE_LINE_RE.match(line)
        if m:
            d = dm_date(m.group(1), m.group(2), today)
            dated.append((d, m.group(3), line))
            continue
        st = stick_from_text(line)
        if st:
            if default_stick and default_stick != st:
                warnings.append(f"conflicting stick wording: {line!r}")
            default_stick = default_stick or st
        elif TIMEISH_RE.search(line):
            warnings.append(f"could not read {line!r}")

    if not dated:
        if any(NO_TIMES_RE.search(x) for x in lines) and not warnings:
            lo, hi = recurring_window(today)
            return AdapterResult(
                covers_from=lo.isoformat(), covers_to=hi.isoformat(), source_updated=None,
                halls={HALL_ID: HallResult(hall_id=HALL_ID, name=HALL_NAME, rink=None)},
                empty_reason="The club page lists no public-skating dates right now",
            )
        raise ParseError("No dated lines in the public-skating article – has the page layout changed?")

    hall = HallResult(hall_id=HALL_ID, name=HALL_NAME, rink=None)
    seen: set[tuple] = set()
    for d, rest, line in dated:
        if d < today:
            continue  # already past; not covered
        try:
            ranges = time_ranges(rest)
        except BadTimeRange as exc:
            warnings.append(f"bad time in {line!r}: {exc}")
            continue
        if not ranges:
            if not CLOSED_RE.search(rest):
                warnings.append(f"could not read {line!r}")
            continue
        own = stick_from_text(rest)
        stick = own or default_stick or STICK_UNKNOWN
        for s, e in ranges:
            if s < "05:00":
                warnings.append(f"time outside 05-24 in {line!r}")
                continue
            key = (d, s, e, stick)
            if key in seen:
                continue
            seen.add(key)
            hall.sessions.append(Session(date=d.isoformat(), start=s, end=e, stick=stick,
                                         source_url=SOURCE_URL))
    hall.sessions.sort(key=lambda x: (x.date, x.start, x.end))

    last = max(d for d, _, _ in dated)
    if last < today:
        warnings.append(f"all listed dates are in the past (last {last}); page may be stale")
        last = today
    return AdapterResult(
        covers_from=today.isoformat(),
        covers_to=last.isoformat(),
        source_updated=None,
        halls={HALL_ID: hall},
        warnings=warnings,
    )
