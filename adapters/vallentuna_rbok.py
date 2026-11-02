"""Vallentuna kommun: one Rbok board for Vallentuna IP ishall A and B, about four weeks ahead.
See rbok.py for the board format."""
from __future__ import annotations

from datetime import date

from . import rbok
from .common import AdapterResult

NAME = "vallentuna-rbok"
MUNICIPALITY = "Vallentuna"
HOST = "vallentuna.rbok.se"
BOARDS = ["7c6bd9c2-3623-4eae-9005-09813289c00d"]
SOURCE_URL = rbok.board_url(HOST, BOARDS[0])

HALLS = [
    rbok.Hall("Vallentuna IP / Ishall A", "vallentuna-vallentuna-ip-ishall-a", "Vallentuna IP", "Ishall A"),
    rbok.Hall("Vallentuna IP / Ishall B", "vallentuna-vallentuna-ip-ishall-b", "Vallentuna IP", "Ishall B"),
]


def fetch() -> dict[str, str]:
    return rbok.fetch_boards(HOST, BOARDS)


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    return rbok.parse_boards(raw, today, host=HOST, halls=HALLS, muni="vallentuna")
