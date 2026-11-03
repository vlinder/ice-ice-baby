"""Lidingö stad: lidingo.se "Skridsko och ishallar"

Page layout (as of Nov 2026), a WordPress page; inside <main>:

    <h2 class="wp-block-heading">Exakt arena – allmänhetens åkning</h2>
    <p>... Exakt arena är öppen från vecka 32 2026 och erbjuder allmänhetens åkning
       från vecka 34 fram till och med vecka 21 2027. ...</p>
    <div class="modularity-mod-table">
      <h2 class="c-table__title">Allmänhetens åkning i Exakt arena</h2>
      <table> Veckodag | Tid | Kommentar </table>          weekly template
      <dialog class="c-table__modal"> ... same table again ... </dialog>   fullscreen copy
    </div>
    <h3>Avvikande tider under hösten 2026/våren 2027</h3>
    <p>Allmänhetens åkning utgår:<br>* Röda dagar har vi stängt.<br>
       * 22 november, 14 februari, 7 april, 8 april, 9 april</p>

The same pattern repeats for "Lidingövallens rink" (outdoor rink, "öppen från höstlovet
till och med vecka 9", weather permitting) and "Exakt annex" ("öppen från vecka 40 2026
till och med vecka 13 2027"). The other h2 sections (Kottlasjön, Grönstaviken, Isbiten)
have no times and are not emitted.

Each table is printed twice: once in the page and once inside the fullscreen <dialog>.
Only the in-page table is read; if the dialog copy differs we warn. A row with an empty
"Veckodag" cell continues the previous weekday.

The weekly template is expanded over `recurring_window` cut at the hall's stated season
(whole ISO weeks; "höstlovet" = week 44). Outside the stated season the hall is closed,
so `covers` is the full `recurring_window(today)` even when a season ends inside it.
"Allmänhetens åkning utgår" dates are skipped, and "Röda dagar har vi stängt" skips Swedish
public holidays (computed here; ordinary Sundays are not treated as red days).

Stick: "Med klubba och puck" -> with_stick, "Utan klubba och puck" -> no_stick.
"Med/utan klubba och puck" (you may skate with or without stick at the same time)
is mapped to with_stick, keeping the wording in `note`.

The page only shows "Publiceringsdatum" (first publication), not an update stamp,
so source_updated is None.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from bs4 import BeautifulSoup, Tag

from .common import (
    STICK_UNKNOWN, STICK_WITH,
    AdapterResult, BadTimeRange, HallResult, ParseError, Session, Slot,
    expand_weekly, http_get, infer_year, iso_week_monday, iso_week_sunday, month_number,
    norm_space, recurring_window, slugify, stick_from_text, time_ranges,
    week_year, weekday_number,
)

NAME = "lidingo-web"
MUNICIPALITY = "Lidingö"
SOURCE_URL = "https://lidingo.se/kultur-fritid/idrott-och-friluftsliv/skridsko-och-ishallar/"

HALLS = ["Exakt arena", "Lidingövallens rink", "Exakt annex"]
HOSTLOV_WEEK = 44

SECTION_RE = re.compile(r"^(.+?)\s*[–-]\s*allmänhetens\s+åkning$", re.I)
SEASON_RE = re.compile(
    r"från\s+(?:vecka\s+(\d{1,2})(?:\s+(\d{4}))?|(höstlovet))\s+(?:fram\s+)?till\s+och\s+med\s+"
    r"vecka\s+(\d{1,2})(?:\s+(\d{4}))?",
    re.I,
)
DEVIATION_RE = re.compile(r"avvikande\s+tider(?:.*?(\d{4})\s*/\s*\D*?(\d{4}))?", re.I)
DATE_RE = re.compile(r"(\d{1,2})\s+([a-zåäö]+)", re.I)
MIXED_RE = re.compile(r"\bmed\s*/\s*utan\b", re.I)


def fetch() -> dict[str, str]:
    return {"page": http_get(SOURCE_URL)}


def hall_id(name: str) -> str:
    return "lidingo-" + slugify(name)


# --- Swedish public holidays ("röda dagar") --------------------------------

def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _first_saturday(year: int, month: int, day: int) -> date:
    d = date(year, month, day)
    return d + timedelta(days=(5 - d.weekday()) % 7)


def swedish_public_holidays(year: int) -> set[date]:
    e = _easter(year)
    return {
        date(year, 1, 1), date(year, 1, 6),
        e - timedelta(days=2), e, e + timedelta(days=1),      # långfredag, påsk
        date(year, 5, 1),
        e + timedelta(days=39), e + timedelta(days=49),       # Kristi himmelsfärd, pingst
        date(year, 6, 6),
        _first_saturday(year, 6, 20),                         # midsommardagen
        _first_saturday(year, 10, 31),                        # alla helgons dag
        date(year, 12, 25), date(year, 12, 26),
    }


# --- parsing -----------------------------------------------------------------

def _cell(el: Tag) -> str:
    return norm_space(el.get_text(" "))


def _rows(table: Tag) -> list[list[str]]:
    out = []
    for tr in table.find_all("tr"):
        cells = [_cell(c) for c in tr.find_all(["th", "td"])]
        if cells and not (tr.find_parent("thead") or cells[:2] == ["Veckodag", "Tid"]):
            out.append(cells)
    return out


def _season(text: str, today: date) -> tuple[date, date] | None:
    m = SEASON_RE.search(text)
    if not m:
        return None
    w1, y1, hostlov, w2, y2 = m.groups()
    w1 = HOSTLOV_WEEK if hostlov else int(w1)
    w2 = int(w2)
    end_year = int(y2) if y2 else week_year(w2, today)
    start_year = int(y1) if y1 else (end_year if w1 <= w2 else end_year - 1)
    try:
        return iso_week_monday(start_year, w1), iso_week_sunday(end_year, w2)
    except ValueError:
        raise ParseError(f"Invalid season weeks in {m.group(0)!r}") from None


def _stick(comment: str) -> tuple[str, str | None]:
    if MIXED_RE.search(comment):
        return STICK_WITH, comment.rstrip(".")
    s = stick_from_text(comment)
    if s is None:
        return STICK_UNKNOWN, comment or None
    return s, None


class _Section:
    def __init__(self, hall: str):
        self.hall = hall
        self.text: list[str] = []
        self.table: Tag | None = None
        self.dialog_tables: list[Tag] = []
        self.in_deviations = False
        self.deviation_years: tuple[int, int] | None = None
        self.deviation_lines: list[str] = []


def _collect(root: Tag) -> dict[str, _Section]:
    sections: dict[str, _Section] = {}
    cur: _Section | None = None
    for el in root.find_all(["h2", "h3", "p", "table"]):
        if el.find_parent(class_="contact-card-panel"):
            continue
        in_dialog = el.find_parent("dialog") is not None
        classes = el.get("class") or []
        if el.name == "h2" and "wp-block-heading" in classes:
            m = SECTION_RE.match(_cell(el))
            name = m.group(1) if m else None
            if name in HALLS:
                if name in sections:
                    raise ParseError(f"Two sections for {name}")
                cur = sections[name] = _Section(name)
            else:
                cur = None
            continue
        if cur is None or el.name == "h2":
            continue
        if el.name == "table":
            if in_dialog:
                cur.dialog_tables.append(el)
            elif cur.table is not None:
                raise ParseError(f"{cur.hall}: more than one schedule table")
            else:
                cur.table = el
            continue
        if in_dialog:
            continue
        if el.name == "h3":
            dm = DEVIATION_RE.search(_cell(el))
            cur.in_deviations = dm is not None
            if dm and dm.group(1):
                cur.deviation_years = (int(dm.group(1)), int(dm.group(2)))
            continue
        for br in el.find_all("br"):
            br.replace_with("\n")
        lines = [norm_space(x) for x in el.get_text("").split("\n")]
        lines = [x for x in lines if x]
        (cur.deviation_lines if cur.in_deviations else cur.text).extend(lines)
    return sections


def _closed_dates(sec: _Section, window: tuple[date, date], today: date,
                  warnings: list[str]) -> set[date]:
    closed: set[date] = set()
    for line in sec.deviation_lines:
        if not line.startswith("*"):
            continue  # "Aktuell bokningsbild ...", "Allmänhetens åkning utgår:"
        item = line.lstrip("* ").strip()
        if re.search(r"röda\s+dagar", item, re.I):
            for y in range(window[0].year, window[1].year + 1):
                closed |= swedish_public_holidays(y)
            continue
        parts = [p.strip() for p in re.split(r",|\boch\b", item) if p.strip()]
        for part in parts:
            m = DATE_RE.fullmatch(part)
            try:
                if not m:
                    raise ParseError(part)
                mon = month_number(m.group(2))
                if sec.deviation_years:
                    y = sec.deviation_years[0] if mon >= 7 else sec.deviation_years[1]
                else:
                    y = infer_year(mon, int(m.group(1)), today)
                closed.add(date(y, mon, int(m.group(1))))
            except (ParseError, ValueError):
                warnings.append(f"{hall_id(sec.hall)}: could not read deviation {part!r} in {item!r}")
    return closed


CLOSED_RE = re.compile(r"^\(?\s*(stängt|stängd|inställt|inställd|utgår)\b", re.I)


def _row_date(sec: _Section, text: str, today: date) -> date | None:
    """A table row labelled with a date ("3 november") instead of a weekday."""
    m = DATE_RE.fullmatch(text.strip())
    if not m:
        return None
    try:
        mon = month_number(m.group(2))
    except ParseError:
        return None
    if sec.deviation_years:
        y = sec.deviation_years[0] if mon >= 7 else sec.deviation_years[1]
    else:
        y = infer_year(mon, int(m.group(1)), today)
    try:
        return date(y, mon, int(m.group(1)))
    except ValueError:
        return None


def _slots(sec: _Section, warnings: list[str], today: date
           ) -> tuple[list[Slot], dict[date, list[tuple[str, str, str, str | None]]]]:
    """Weekly slots, plus dated rows: date -> its times (empty list = closed that day).

    A dated row replaces the weekly times on its date. If a dated row cannot be read,
    the date still gets an empty list: showing nothing beats showing a cancelled time.
    """
    hid = hall_id(sec.hall)
    rows = _rows(sec.table)
    for dt in sec.dialog_tables:
        if _rows(dt) != rows:
            warnings.append(f"{hid}: fullscreen copy of the table differs from the page table")
    slots: list[Slot] = []
    dated: dict[date, list[tuple[str, str, str, str | None]]] = {}
    wd: int | None = None
    for cells in rows:
        if len(cells) != 3:
            raise ParseError(f"{hid}: unexpected table row {cells!r}")
        day, times, comment = cells
        d = _row_date(sec, day, today) if day else None
        if d is not None:
            # "3 november | Stängt |", or a dated time that replaces the weekly ones
            entries = dated.setdefault(d, [])
            if CLOSED_RE.match(times) and not re.search(r"\d", times):
                continue
            try:
                ranges = time_ranges(times)
            except BadTimeRange:
                ranges = []
            if not ranges:
                warnings.append(f"{hid}: could not read dated row {' | '.join(cells)!r}; no times shown that day")
                continue
            stick, note = _stick(comment)
            entries += [(s, e, stick, note) for s, e in ranges]
            wd = None  # a following row without a day label belongs to no weekday
            continue
        if day:
            wd = weekday_number(day)
            if wd is None:
                warnings.append(f"{hid}: unknown weekday in row {cells!r}")
                continue
        if wd is None:
            warnings.append(f"{hid}: row without weekday {cells!r}")
            continue
        try:
            ranges = time_ranges(times)
        except BadTimeRange as exc:
            warnings.append(f"{hid}: could not read {cells!r} ({exc})")
            continue
        if not ranges:
            warnings.append(f"{hid}: could not read {cells!r}")
            continue
        stick, note = _stick(comment)
        for s, e in ranges:
            slots.append(Slot(weekday=wd, start=s, end=e, stick=stick, note=note))
    return slots, dated


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    soup = BeautifulSoup(raw["page"], "lxml")
    root = soup.find("main") or soup.body or soup
    sections = _collect(root)
    missing = [h for h in HALLS if h not in sections]
    if missing:
        raise ParseError(f"Hall sections missing: {missing} - has the page layout changed?")

    warnings: list[str] = []
    horizon = recurring_window(today)
    halls: dict[str, HallResult] = {}
    for name in HALLS:
        sec = sections[name]
        hid = hall_id(name)
        season = _season(" ".join(sec.text), today)
        if season is None:
            raise ParseError(f"{name}: no season ('från vecka N till och med vecka M') found")
        window = recurring_window(today, *season)
        hr = HallResult(hall_id=hid, name=name, rink=None)
        if sec.table is None:
            if window is not None:
                warnings.append(f"{hid}: no schedule table although the season is on")
                continue
            halls[hid] = hr  # out of season: closed
            continue
        slots, dated = _slots(sec, warnings, today)
        if window is not None:
            if not slots:
                raise ParseError(f"{name}: schedule table has no times")
            skip = _closed_dates(sec, window, today, warnings) | set(dated)
            sessions = expand_weekly(slots, window[0], window[1], SOURCE_URL, skip=skip)
            sessions += [Session(date=d.isoformat(), start=s, end=e, stick=st, source_url=SOURCE_URL, note=n)
                         for d, entries in dated.items() if window[0] <= d <= window[1]
                         for s, e, st, n in entries]
            hr.sessions = _dedupe(sessions)
        halls[hid] = hr

    total = sum(len(h.sessions) for h in halls.values())
    empty_reason = None
    if total == 0 and all(recurring_window(today, *_season(" ".join(sections[n].text), today)) is None
                          for n in HALLS):
        empty_reason = "All rinks are outside their stated public-skating season"
    return AdapterResult(
        covers_from=horizon[0].isoformat(), covers_to=horizon[1].isoformat(),
        source_updated=None, halls=halls, warnings=warnings, empty_reason=empty_reason,
    )


def _dedupe(sessions: list[Session]) -> list[Session]:
    seen, out = set(), []
    for s in sorted(sessions, key=lambda x: (x.date, x.start, x.end, x.stick)):
        k = (s.date, s.start, s.end, s.stick)
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out
