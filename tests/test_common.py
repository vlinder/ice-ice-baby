from datetime import date

import pytest

from adapters.common import (
    BadTimeRange, Slot, expand_weekly, recurring_window, stick_from_text, time_ranges, weekday_number, week_year,
)


@pytest.mark.parametrize("text, expected", [
    ("14.00-15.45", [("14:00", "15:45")]),
    ("Klockan 08.00–15.15", [("08:00", "15:15")]),
    ("kl. 1300-14.50", [("13:00", "14:50")]),
    ("Lördag 12-13.50, utan klubba", [("12:00", "13:50")]),
    ("12-14, 14.10-15.50", [("12:00", "14:00"), ("14:10", "15:50")]),
    ("10.00 – 10.45", [("10:00", "10:45")]),
    # typos and things that are not times are not half-read
    ("15-30-17.50", []), ("11-00–12.50", []), ("13.300–15.15", []), ("09.00-14-00", []),
    ("2026-11-02", []), ("26-30 oktober", []), ("Vecka 44 (26/10-1/11)", []), ("Tel 08-555 592 64", []),
])
def test_time_ranges(text, expected):
    assert time_ranges(text) == expected


def test_impossible_time_range_raises():
    with pytest.raises(BadTimeRange):
        time_ranges("16.00-14.00")


@pytest.mark.parametrize("text, expected", [
    ("Med puck & klubba", "with_stick"), ("Klubba tillåten", "with_stick"), ("Åkning med klubba", "with_stick"),
    ("Utan Puck&klubba", "no_stick"), ("ingen klubba", "no_stick"), ("Klubba ej tillåten", "no_stick"),
    ("Inte tillåtet med klubba, puck och bollar", "no_stick"), ("utan puck och boll", "no_stick"),
    ("halva med klubba/puck, halva utan", "shared"), ("Delad is", "shared"),
    ("BUSSTER", None),
])
def test_stick_from_text(text, expected):
    assert stick_from_text(text) == expected


def test_weekday_number():
    assert [weekday_number(w) for w in ["Måndagar", "måndag", "Mån", "Söndagen", "Tors", "Fredag:"]] == [0, 0, 0, 6, 3, 4]
    assert weekday_number("Klockan") is None


def test_recurring_window_and_expand():
    today = date(2026, 11, 2)
    assert recurring_window(today) == (today, date(2026, 11, 29))
    assert recurring_window(today, season_to=date(2026, 11, 10)) == (today, date(2026, 11, 10))
    assert recurring_window(today, season_from=date(2027, 1, 1)) is None
    out = expand_weekly([Slot(0, "14:00", "15:00", "shared")], today, date(2026, 11, 16), "u",
                        skip={date(2026, 11, 9)})
    assert [s.date for s in out] == ["2026-11-02", "2026-11-16"]


def test_week_year():
    assert week_year(12, date(2026, 11, 2)) == 2027
    assert week_year(45, date(2026, 11, 2)) == 2026
