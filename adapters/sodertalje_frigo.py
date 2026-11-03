"""Södertälje kommun: one FRI-GO booking list per rink (booking type 9, the next
7 days). The lists are fixed by a checksum, so the 7-day window cannot be widened.
In early November 2026 all four lists were empty ("Inga aktuella bokningar att
visa"), which is reported as an explicit empty source. The column set
(tid,boktyp,verk,eget) has not been seen with rows yet: if rows appear and look
different from Ekerö's list, the parser warns or fails rather than guessing.
See frigo.py for the list format."""
from __future__ import annotations

from datetime import date

from . import frigo
from .common import AdapterResult

NAME = "sodertalje-frigo"
MUNICIPALITY = "Södertälje"
_BASE = "https://sodertalje.fri-go.se/bokning/lista.aspx?dagar=7&typer=9&falt=tid,boktyp,verk,eget"
LISTS = {
    "sodertalje-bst-rinken": ("BST-rinken", f"{_BASE}&obj=360&c=8627"),
    "sodertalje-jarna-ip-ishall-a": ("Järna IP", f"{_BASE}&obj=35&c=7508"),
    "sodertalje-jarna-ip-ishall-b": ("Järna IP", f"{_BASE}&obj=403&c=8181"),
    "sodertalje-manskensrinken": ("Månskensrinken", f"{_BASE}&obj=34&c=7285"),
}
SOURCE_URL = LISTS["sodertalje-jarna-ip-ishall-a"][1]

HALLS = [
    frigo.Hall(hid, name, {"sodertalje-jarna-ip-ishall-a": "Ishall A",
                           "sodertalje-jarna-ip-ishall-b": "Ishall B"}.get(hid), url=url)
    for hid, (name, url) in LISTS.items()
]


def fetch() -> dict[str, str]:
    return frigo.fetch_lists([url for _, url in LISTS.values()])


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    return frigo.parse_lists(raw, today, halls=HALLS, muni="sodertalje")
