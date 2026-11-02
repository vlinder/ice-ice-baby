"""Norrtälje kommun: one Rbok board for Norrtälje sportcentrum. It lists the ice hall
(and the outdoor bandy rink in winter); the board is usually full (100 rows).
See rbok.py for the board format."""
from __future__ import annotations

from datetime import date

from . import rbok
from .common import AdapterResult

NAME = "norrtalje-rbok"
MUNICIPALITY = "Norrtälje"
HOST = "norrtalje.rbok.se"
BOARDS = ["593f6fb0-60ac-48b9-afb0-4dd8f5ea44db"]
SOURCE_URL = rbok.board_url(HOST, BOARDS[0])

HALLS = [
    rbok.Hall("Sportcentrum / Ishall", "norrtalje-sportcentrum-ishall", "Norrtälje sportcentrum", "Ishall"),
    rbok.Hall("Sportcentrum / Bandybana", "norrtalje-sportcentrum-bandybana", "Norrtälje sportcentrum", "Bandybana"),
]


def fetch() -> dict[str, str]:
    return rbok.fetch_boards(HOST, BOARDS)


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    return rbok.parse_boards(raw, today, host=HOST, halls=HALLS, muni="norrtalje")
