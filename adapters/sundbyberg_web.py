"""Sundbybergs stad: sundbyberg.se, "Ishall och isbanor" (Tulehallen).

Page layout (as of Nov 2026): prose under "Allmänhetens åkning i Tulehallen"
says the rink is always split ("planen uppdelad. På ena halvan är klubbor
tillåtna, på den andra ... inte"), so every session gets stick "shared"
(taken from that paragraph with `stick_from_text`; unknown if it disappears).
The next paragraph gives the season: "vardagar från vecka 35 till och med
vecka 17 samt helger från vecka 41 till och med 17".

The times are a Sitevision "openingHours-week" widget
(<div class="sv-se-soleil-openingHours-week">). The server renders 7 days
("Idag (Måndag 2 november) Klockan 08.00–15.15", ...) with Föregående/Nästa
buttons; the buttons do not load anything, the widget recomputes from the
JSON embedded in the same div:

    AppRegistry.registerInitialState('<portlet id>', {
        "openHours": [[{"open": "08:00", "close": "15:15", "text": ""}], ...],   # Mon..Sun
        "deviantHours": [{"date": "2026-11-07", "open": "12:00", "close": "15:15",
                          "open2": "", "close2": "", ..., "closed": false}, ...],
        "currentWeek": {"week": 45, "year": 2026, ...},
        "nextWeeks": [{"week": 46, "year": 2026}, ..., {"week": 50, ...}], ...})

So we parse the JSON: a dated "deviantHours" entry replaces the weekly template
on its date ("closed": true -> no public skating); other days use "openHours",
but only inside the season stated in the prose (weekdays / weekends separately).
`covers` runs from today to the Sunday of the last week the widget lets a
visitor page to ("nextWeeks"), which is what a person can see on the page.
The 7 rendered days are cross-checked against the computed sessions; any
mismatch becomes a warning.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from bs4 import BeautifulSoup, Tag

from .common import (
    STICK_UNKNOWN, AdapterResult, BadTimeRange, HallResult, ParseError, Session,
    http_get, iso_week_monday, iso_week_sunday, norm_space, slugify, stick_from_text,
    sv_date, time_ranges, weekday_number,
)

NAME = "sundbyberg-web"
MUNICIPALITY = "Sundbyberg"
SOURCE_URL = ("https://www.sundbyberg.se/uppleva-och-gora/idrott-motion-och-friluftsliv/"
              "hallar-och-idrottsanlaggningar/ishall-och-isbanor")

HALL_NAME = "Tulehallen"
HALL_ID = "sundbyberg-" + slugify(HALL_NAME)

HEADING_RE = re.compile(r"^allmänhetens åkning i tulehallen$", re.I)
WEEKDAY_SEASON_RE = re.compile(r"vardagar\s+från\s+vecka\s+(\d{1,2})\s+till\s+och\s+med\s+(?:vecka\s+)?(\d{1,2})", re.I)
WEEKEND_SEASON_RE = re.compile(r"helger\s+från\s+vecka\s+(\d{1,2})\s+till\s+och\s+med\s+(?:vecka\s+)?(\d{1,2})", re.I)
UPDATED_RE = re.compile(r"senast uppdaterad:?\s*(\d{1,2})\s+([a-zåäö]+)\s+(\d{4})", re.I)
HHMM_RE = re.compile(r"^([01]?\d|2[0-4]):([0-5]\d)$")
RENDERED_DAY_RE = re.compile(r"([a-zåäö]+)\s+(\d{1,2})\s+([a-zåäö]+)", re.I)


def fetch() -> dict[str, str]:
    return {"page": http_get(SOURCE_URL)}


def _state(widget: Tag) -> dict:
    for script in widget.find_all("script"):
        text = script.string or script.get_text()
        i = text.find("registerInitialState(")
        if i < 0:
            continue
        j = text.find("{", i)
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[j:])
        except (ValueError, IndexError) as exc:
            raise ParseError(f"Opening-hours widget state is not JSON: {exc}") from None
        if "openHours" in obj:
            return obj
    raise ParseError("Opening-hours widget has no initial state with 'openHours'")


def _season(text: str, regex: re.Pattern, d: date) -> tuple[date, date] | None:
    """The season ("från vecka 35 till och med vecka 17") that contains or follows d."""
    m = regex.search(text)
    if not m:
        return None
    w1, w2 = int(m.group(1)), int(m.group(2))
    for y in (d.isocalendar()[0] - 1, d.isocalendar()[0]):
        start = iso_week_monday(y, w1)
        end = iso_week_sunday(y + (1 if w2 < w1 else 0), w2)
        if d <= end:
            return start, end
    return None


def _hhmm(v) -> str | None:
    if v in (None, ""):
        return None
    m = HHMM_RE.match(str(v).strip())
    if not m:
        raise ValueError(f"bad time {v!r}")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _pairs(entry: dict) -> list[tuple[str | None, str | None, str]]:
    """deviantHours entry -> [(open, close, text)] for open/open2..open5."""
    out = []
    for suffix in ("", "2", "3", "4", "5"):
        out.append((entry.get("open" + suffix), entry.get("close" + suffix), entry.get("text" + suffix) or ""))
    return out


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    soup = BeautifulSoup(raw["page"], "lxml")
    heading = next((h for h in soup.find_all(["h2", "h3"]) if HEADING_RE.match(norm_space(h.get_text()))), None)
    if heading is None:
        raise ParseError("Heading 'Allmänhetens åkning i Tulehallen' not found – layout changed?")
    intro = heading.find_next_sibling("p")
    stick_default = stick_from_text(norm_space(intro.get_text(" "))) if intro else None
    prose = norm_space(heading.parent.get_text(" "))

    widget = soup.select_one("div.sv-se-soleil-openingHours-week")
    if widget is None:
        raise ParseError("Opening-hours widget not found – layout changed?")
    state = _state(widget)
    warnings: list[str] = []

    week_tmpl = state.get("openHours")
    if not isinstance(week_tmpl, list) or len(week_tmpl) != 7:
        raise ParseError("openHours is not a 7-day list")
    deviant: dict[str, dict] = {}
    for e in state.get("deviantHours") or []:
        if not isinstance(e, dict) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(e.get("date", ""))):
            raise ParseError(f"Unexpected deviantHours entry {e!r}")
        deviant[e["date"]] = e
    try:
        next_weeks = state["nextWeeks"]
        last = max((int(w["year"]), int(w["week"])) for w in next_weeks) if next_weeks else \
            (int(state["currentWeek"]["year"]), int(state["currentWeek"]["week"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ParseError(f"Widget week navigation missing: {exc}") from None
    covers_from, covers_to = today, iso_week_sunday(*last)
    if covers_to < covers_from:
        raise ParseError(f"Widget's last week {last} is before today")

    hall = HallResult(hall_id=HALL_ID, name=HALL_NAME, rink=None)

    def add(d: date, o, c, text: str) -> None:
        try:
            s, e = _hhmm(o), _hhmm(c)
        except ValueError as exc:
            warnings.append(f"{HALL_ID}: {d}: {exc}")
            return
        if s is None and e is None:
            return
        if s is None or e is None or e <= s or s < "05:00" or e > "24:00":
            warnings.append(f"{HALL_ID}: {d}: unusable time {o!r}–{c!r}")
            return
        text = norm_space(text)
        stick = stick_from_text(text) if text else None
        stick = stick or stick_default or STICK_UNKNOWN
        key = (d.isoformat(), s, e, stick)
        if any((x.date, x.start, x.end, x.stick) == key for x in hall.sessions):
            return
        hall.sessions.append(Session(date=d.isoformat(), start=s, end=e, stick=stick,
                                     source_url=SOURCE_URL, note=text or None))

    d = covers_from
    while d <= covers_to:
        dev = deviant.get(d.isoformat())
        if dev is not None:
            if not dev.get("closed"):
                for o, c, text in _pairs(dev):
                    add(d, o, c, text)
        else:
            season_re = WEEKEND_SEASON_RE if d.weekday() >= 5 else WEEKDAY_SEASON_RE
            if season_re.search(prose):
                season = _season(prose, season_re, d)
                in_season = season is not None and season[0] <= d <= season[1]
            else:
                in_season = True  # no season stated: the widget's template applies
            if in_season:
                for slot in week_tmpl[d.weekday()] or []:
                    if not isinstance(slot, dict):
                        raise ParseError(f"Unexpected openHours slot {slot!r}")
                    add(d, slot.get("open"), slot.get("close"), slot.get("text") or "")
        d += timedelta(days=1)

    # Cross-check against the server-rendered days.
    for li in widget.select("li.date"):
        label = norm_space((li.select_one(".date-text") or li).get_text(" "))
        m = RENDERED_DAY_RE.search(label.replace("Idag", " ").replace("(", " "))
        if not m or weekday_number(m.group(1)) is None:
            warnings.append(f"{HALL_ID}: rendered day label not understood: {label!r}")
            continue
        rd = sv_date(m.group(2), m.group(3), today)
        if not (covers_from <= rd <= covers_to):
            continue
        shown = []
        for t in li.select(".date-time"):
            try:
                shown += time_ranges(norm_space(t.get_text(" ")))
            except BadTimeRange as exc:
                warnings.append(f"{HALL_ID}: rendered time on {rd} not understood ({exc})")
        ours = sorted((s.start, s.end) for s in hall.sessions if s.date == rd.isoformat())
        if sorted(shown) != ours:
            warnings.append(f"{HALL_ID}: {rd}: page shows {sorted(shown)} but widget data gives {ours}")

    if stick_default is None:
        warnings.append(f"{HALL_ID}: stick wording under 'Allmänhetens åkning i Tulehallen' not found")

    updated = None
    m = UPDATED_RE.search(norm_space(soup.get_text(" ")))
    if m:
        updated = sv_date(m.group(1), m.group(2), today, m.group(3)).isoformat() + "T00:00"

    hall.sessions.sort(key=lambda s: (s.date, s.start, s.end))
    return AdapterResult(
        covers_from=covers_from.isoformat(),
        covers_to=covers_to.isoformat(),
        source_updated=updated,
        halls={HALL_ID: hall},
        warnings=warnings,
    )
