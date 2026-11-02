"""Haninge kommun: one Rbok board per hall (Hanvedens IP, Torvalla IP ishall 1 and 2),
each reaching a different number of weeks ahead, so every hall gets its own
`covers`. Hanveden is sometimes split into Halvplan-A (with sticks) and
Halvplan-B (without) at the same time, which becomes one "shared" session.
See rbok.py for the board format."""
from __future__ import annotations

from datetime import date

from . import rbok
from .common import AdapterResult

NAME = "haninge-rbok"
MUNICIPALITY = "Haninge"
HOST = "haninge.rbok.se"
BOARDS = [
    "e8330e15-76fd-4bcf-9dd2-93c54dae9559",  # Hanvedens ishall
    "9042795d-4b52-44f5-be64-e80657db8ef8",  # Torvalla ishall 1
    "71c94175-66c8-41fd-8b4b-d8a41729e78b",  # Torvalla ishall 2
]
SOURCE_URL = rbok.board_url(HOST, BOARDS[0])

HALLS = [
    rbok.Hall("Hanvedens IP / Ishall", "haninge-hanvedens-ip", "Hanvedens IP", "Ishall"),
    rbok.Hall("Torvalla IP / Ishall 1", "haninge-torvalla-ip-ishall-1", "Torvalla IP", "Ishall 1"),
    rbok.Hall("Torvalla IP / Ishall 2", "haninge-torvalla-ip-ishall-2", "Torvalla IP", "Ishall 2"),
]


def fetch() -> dict[str, str]:
    return rbok.fetch_boards(HOST, BOARDS)


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    return rbok.parse_boards(raw, today, host=HOST, halls=HALLS, muni="haninge")
