"""Sollentuna kommun: sollentuna.se, "Skridskoåkning i Sollentuna".

Page layout (as of Nov 2026): one editor text block. Under the heading

    <h2>Allmänhetens åkning på Sollentunavallen</h2>

the page lists one or more weeks, each as

    <h2>Vecka 45:</h2>                       (or "Vecka 44 HÖSTLOV")
    <h3>Måndag</h3>
    <ul><li>B-hallen: klockan 10.00-12.30 - Med klubba.</li> ...</ul>
    <h3>Tisdag</h3>
    <ul><li>Ingen allmänhetens åkning!</li></ul>
    ...

until the next non-"Vecka" <h2> ("Boka ishallarna och bandyplanen"). The two
halls are Sollentunavallen A-hallen and B-hallen; a hall without a line on a
listed day has no public skating that day. Weeks are often published one day
at a time, so a week only covers Monday up to its last listed weekday.

Judgment calls:
- Weeks that ended before today are skipped without parsing.
- A time followed by a linked or ALL-CAPS label instead of stick wording
  (e.g. "A-hallen: klockan 11.00-14.45 - <a>BUSSTER</a>", the municipality's
  holiday activity programme) is a separate, non-public activity: it is skipped
  with a warning (never silently dropped). Other unknown wording gives
  stick "unknown" with the wording in `note`.
- Both A-hallen and B-hallen are always emitted (the page's schedule covers
  both halls of Sollentunavallen), plus any other "X-hallen" the page names.
- If the page lists only past weeks, the halls are emitted with no sessions and
  `covers` set to the last listed week (i.e. in the past: unknown from today).
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from bs4 import BeautifulSoup, Tag

from .common import (
    STICK_UNKNOWN, AdapterResult, BadTimeRange, HallResult, ParseError, Session,
    TIME_RANGE_RE, http_get, iso_week_monday, norm_space, recurring_window, slugify,
    stick_from_text, sv_date, time_ranges, week_year, weekday_number,
)

NAME = "sollentuna-web"
MUNICIPALITY = "Sollentuna"
SOURCE_URL = "https://www.sollentuna.se/uppleva--gora/idrott-motion-och-friluftsliv/skridskoakning"

FACILITY = "Sollentunavallen"
KNOWN_RINKS = ["A-hallen", "B-hallen"]

SECTION_RE = re.compile(r"^allmänhetens åkning på sollentunavallen", re.I)
WEEK_RE = re.compile(r"^vecka\s+(\d{1,2})(?!\s*[–\-]\s*\d)\b", re.I)
WEEK_START_RE = re.compile(r"^vecka\b", re.I)
LINE_RE = re.compile(r"^([A-ZÅÄÖ])-hallen\s*:\s*(.*)$", re.I)
NONE_RE = re.compile(r"ingen\s+allmänhetens\s+åkning|inställ|stängt|stängd", re.I)
CLOSED_SEASON_RE = re.compile(r"ingen allmänhetens åkning|säsongen är (slut|avslutad)|stängt för säsongen", re.I)
UPDATED_RE = re.compile(r"sidan uppdaterades\s+(\d{1,2})\s+([a-zåäö]+)\s+(\d{4})", re.I)


def fetch() -> dict[str, str]:
    return {"page": http_get(SOURCE_URL)}


def hall_id(rink: str) -> str:
    return "sollentuna-" + slugify(FACILITY) + "-" + slugify(rink)


def _source_updated(soup: BeautifulSoup, today: date) -> str | None:
    t = soup.select_one(".c-page-info time[datetime]")
    if t and re.fullmatch(r"\d{4}-\d{2}-\d{2}", t["datetime"].strip()):
        return t["datetime"].strip() + "T00:00"
    m = UPDATED_RE.search(norm_space(soup.get_text(" ")))
    if m:
        return sv_date(m.group(1), m.group(2), today, m.group(3)).isoformat() + "T00:00"
    return None


class _Halls:
    """Collects sessions per hall, deduplicating and checking hours."""

    def __init__(self) -> None:
        self.halls: dict[str, HallResult] = {}
        self.warnings: list[str] = []
        for rink in KNOWN_RINKS:
            self.hall(rink)

    def hall(self, rink: str) -> HallResult:
        hid = hall_id(rink)
        return self.halls.setdefault(hid, HallResult(hall_id=hid, name=FACILITY, rink=rink))

    def add(self, rink: str, day: date, start: str, end: str, stick: str, note: str | None) -> None:
        hall = self.hall(rink)
        if start < "05:00" or end > "24:00":
            self.warnings.append(f"{hall.hall_id}: {day} {start}–{end} outside 05–24, skipped")
            return
        key = (day.isoformat(), start, end, stick)
        if any((s.date, s.start, s.end, s.stick) == key for s in hall.sessions):
            return
        hall.sessions.append(Session(date=day.isoformat(), start=start, end=end, stick=stick,
                                     source_url=SOURCE_URL, note=note))


def _parse_line(li: Tag, day: date, out: _Halls) -> None:
    line = norm_space(li.get_text(" "))
    m = LINE_RE.match(line)
    if not m:
        if NONE_RE.search(line) and not TIME_RANGE_RE.search(line):
            return  # "Ingen allmänhetens åkning!"
        out.warnings.append(f"{day}: unexpected line {line!r}")
        return
    rink = m.group(1).upper() + "-hallen"
    rest = m.group(2)
    try:
        ranges = time_ranges(rest)
    except BadTimeRange as exc:
        out.warnings.append(f"{hall_id(rink)} {day}: could not read {line!r} ({exc})")
        return
    if not ranges:
        if NONE_RE.search(rest) and not re.search(r"\d", rest):
            out.hall(rink)
            return
        out.warnings.append(f"{hall_id(rink)} {day}: could not read {line!r}")
        return
    trailing = norm_space(TIME_RANGE_RE.sub(" ", re.sub(r"^\s*klockan\s+", "", rest, flags=re.I)))
    trailing = trailing.strip(" -–.,:!")
    stick = stick_from_text(trailing)
    note = None
    if stick is None:
        if trailing and (li.find("a") is not None or trailing.isupper()):
            out.warnings.append(
                f"{hall_id(rink)} {day}: {'/'.join(s + '–' + e for s, e in ranges)} is marked {trailing!r} "
                "(separate activity, not public skating) – skipped")
            return
        stick, note = STICK_UNKNOWN, (trailing or None)
    for s, e in ranges:
        out.add(rink, day, s, e, stick, note)


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    soup = BeautifulSoup(raw["page"], "lxml")
    heading = next((h for h in soup.find_all("h2") if SECTION_RE.match(norm_space(h.get_text()))), None)
    if heading is None:
        raise ParseError("Heading 'Allmänhetens åkning på Sollentunavallen' not found – layout changed?")

    out = _Halls()
    weeks: list[tuple[date, date]] = []          # (monday, last listed day) of current/future weeks
    past_weeks: list[tuple[date, date]] = []
    monday: date | None = None
    past = False
    day: date | None = None
    listed: list[int] = []
    section_text: list[str] = []

    def close_week() -> None:
        if monday is None:
            return
        last = monday + timedelta(days=max(listed)) if listed else None
        if last is None:
            if not past:
                raise ParseError(f"Week starting {monday} lists no days")
            return
        (past_weeks if past else weeks).append((monday, last))

    for el in heading.find_next_siblings():
        text = norm_space(el.get_text(" "))
        if el.name in ("h1", "h2"):
            m = WEEK_RE.match(text)
            if not m:
                if WEEK_START_RE.match(text):
                    raise ParseError(f"Unrecognised week heading {text!r}")
                break  # end of the skating section
            close_week()
            w = int(m.group(1))
            monday = iso_week_monday(week_year(w, today), w)
            past = monday + timedelta(days=6) < today
            day, listed = None, []
            continue
        if not text:
            continue
        section_text.append(text)
        if monday is None:
            continue  # text before the first week heading
        if el.name in ("h3", "h4"):
            wd = weekday_number(text.split(" ")[0])
            if wd is None:
                raise ParseError(f"Unrecognised day heading {text!r} in week of {monday}")
            day = monday + timedelta(days=wd)
            listed.append(wd)
            continue
        if past:
            continue
        if day is None:
            if TIME_RANGE_RE.search(text):
                out.warnings.append(f"week of {monday}: time outside a day heading: {text!r}")
            continue
        if el.name in ("ul", "ol"):
            for li in el.find_all("li"):
                _parse_line(li, day, out)
        elif TIME_RANGE_RE.search(text) or LINE_RE.match(text):
            out.warnings.append(f"{day}: unexpected line {text!r}")
    close_week()

    empty_reason = None
    if weeks:
        covers_from, covers_to = min(w[0] for w in weeks), max(w[1] for w in weeks)
    elif past_weeks:
        covers_from, covers_to = max(past_weeks)
    elif section_text and CLOSED_SEASON_RE.search(" ".join(section_text)):
        covers_from, covers_to = recurring_window(today)
        empty_reason = "Page lists no public skating weeks (season closed)"
    else:
        raise ParseError("No 'Vecka NN' schedule found under the public skating heading")

    for h in out.halls.values():
        h.sessions.sort(key=lambda s: (s.date, s.start, s.end))
    return AdapterResult(
        covers_from=covers_from.isoformat(),
        covers_to=covers_to.isoformat(),
        source_updated=_source_updated(soup, today),
        halls=out.halls,
        warnings=out.warnings,
        empty_reason=empty_reason,
    )
