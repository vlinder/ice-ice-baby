"""Nacka kommun: one Rbok board for all six public rinks (Nacka IP ishall and istält,
Björknäs ishall and istält, Älta ishall, Saltsjöbadens ishall), about three weeks ahead.
See rbok.py for the board format."""
from __future__ import annotations

from datetime import date

from . import rbok
from .common import AdapterResult

NAME = "nacka-rbok"
MUNICIPALITY = "Nacka"
HOST = "nacka.rbok.se"
BOARDS = ["a4d7b5df-2c80-409f-ac9d-da8d6366284b"]
SOURCE_URL = rbok.board_url(HOST, BOARDS[0])

HALLS = [
    rbok.Hall("Nacka IP / Ishall", "nacka-nacka-ip-ishall", "Nacka IP", "Ishall"),
    rbok.Hall("Nacka IP / Istält", "nacka-nacka-ip-istalt", "Nacka IP", "Istält"),
    rbok.Hall("Björknäs IP / Ishall", "nacka-bjorknas-ip-ishall", "Björknäs IP", "Ishall"),
    rbok.Hall("Björknäs IP / Istält", "nacka-bjorknas-ip-istalt", "Björknäs IP", "Istält"),
    rbok.Hall("Älta ishall / Ishall", "nacka-alta-ishall", "Älta ishall"),
    rbok.Hall("Saltsjöbadens Ishall / Ishall", "nacka-saltsjobadens-ishall", "Saltsjöbadens ishall"),
]


def fetch() -> dict[str, str]:
    return rbok.fetch_boards(HOST, BOARDS)


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    return rbok.parse_boards(raw, today, host=HOST, halls=HALLS, muni="nacka")
