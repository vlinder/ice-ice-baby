"""Järfälla kommun: the public schedule in Järfälla's FRI-GO booking system
(jarfalla.fri-go.se), one request per rink. Public skating is booked with the
activity "Allmänheten" and the stick rule as the booking's label ("Med klubba",
"Utan klubba"). The schedules hold the whole season, so this reaches further
ahead than the municipality's web page, which listed only a few weeks.
See frigo_schema.py for the format."""
from __future__ import annotations

from datetime import date

from . import frigo_schema
from .common import AdapterResult, today_local

NAME = "jarfalla-frigo"
MUNICIPALITY = "Järfälla"
BASE = "https://jarfalla.fri-go.se/bokning/"
SOURCE_URL = BASE + "default.aspx"

RINKS = [
    frigo_schema.Rink(109, "jarfalla-jarfalla-ishall", "Järfälla Ishall"),
    frigo_schema.Rink(42, "jarfalla-kallhalls-ishall", "Kallhälls Ishall"),
]


def fetch() -> dict[str, str]:
    return frigo_schema.fetch_schedules(BASE, RINKS, today_local())


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    return frigo_schema.parse_schedules(raw, today, rinks=RINKS, base=BASE)
