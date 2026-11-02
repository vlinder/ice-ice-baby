"""Tyresö kommun: public skating board in Rbok (see rbok.py for the format).

tyreso.se links to one board that lists both Tyresö ishall and Fontanahallen,
about two weeks ahead, including holiday (lov) times.
"""
from __future__ import annotations

from datetime import date

from . import rbok
from .common import AdapterResult

NAME = "tyreso-rbok"
MUNICIPALITY = "Tyresö"
HOST = "tyreso.rbok.se"
BOARDS = ["fbe27841-479e-4387-8e40-9b6a158436c4"]
SOURCE_URL = rbok.board_url(HOST, BOARDS[0])
# The municipality's own page that links to the board, shown as context.
INFO_URL = "https://www.tyreso.se/uppleva--gora/natur-och-friluftsliv/aka-skridskor/allmanhetens-akning.html"

HALLS = [
    rbok.Hall("Tyresövallen / Tyresö Ishall / Ishall", "tyreso-tyreso-ishall", "Tyresö Ishall", "Tyresövallen"),
    rbok.Hall("Trollbäckens idrottsplats / Fontanahallen / Ishall", "tyreso-fontanahallen", "Fontanahallen",
              "Trollbäckens idrottsplats"),
]


def fetch() -> dict[str, str]:
    return rbok.fetch_boards(HOST, BOARDS)


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    return rbok.parse_boards(raw, today, host=HOST, halls=HALLS, muni="tyreso")
