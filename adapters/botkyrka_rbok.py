"""Botkyrka kommun: one Rbok board, "Istider allmänhetens åkning", for Brantbrinks IP
and Tumba idrottsområde (Ishuset, hall 2). The board is usually full (100 rows,
about seven weeks).
See rbok.py for the board format."""
from __future__ import annotations

from datetime import date

from . import rbok
from .common import AdapterResult

NAME = "botkyrka-rbok"
MUNICIPALITY = "Botkyrka"
HOST = "botkyrka.rbok.se"
BOARDS = ["9cba6a4b-91b5-4dc1-9668-0fbdf7e09b28"]
SOURCE_URL = rbok.board_url(HOST, BOARDS[0])

HALLS = [
    rbok.Hall("Brantbrinks IP / Ishall", "botkyrka-brantbrinks-ip", "Brantbrinks IP", "Ishall"),
    rbok.Hall("Tumba Idrottsområde / Ishuset / Hall 2", "botkyrka-tumba-ishuset-hall-2", "Tumba idrottsområde",
              "Ishuset, hall 2"),
]


def fetch() -> dict[str, str]:
    return rbok.fetch_boards(HOST, BOARDS)


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    return rbok.parse_boards(raw, today, host=HOST, halls=HALLS, muni="botkyrka")
