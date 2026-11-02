"""Ekerö kommun: FRI-GO booking list "Ishallar", 28 days ahead, booking type
"Allmänhetens åkning", for Träkvistavallen (Vikingahallen) and Svanängen IP
(Allhallen). The stick rule is in the free-text column ("Alltid hjälm, Med klubba").
See frigo.py for the list format."""
from __future__ import annotations

from datetime import date

from . import frigo
from .common import AdapterResult

NAME = "ekero-frigo"
MUNICIPALITY = "Ekerö"
SOURCE_URL = ("https://ekero.fri-go.se/bokning/lista.aspx?dagar=28&typer=7"
              "&falt=tid,fullnamn,namn,eget,eget2,delt&schema=Ishallar&c=6937")

HALLS = [
    frigo.Hall("ekero-trakvistavallen", "Träkvistavallen", "Vikingahallen", place="Träkvistavallen"),
    frigo.Hall("ekero-svanangen-ip", "Svanängen IP", "Allhallen", place="Svanängen IP"),
]


def fetch() -> dict[str, str]:
    return frigo.fetch_lists([SOURCE_URL])


def parse(raw: dict[str, str], today: date) -> AdapterResult:
    return frigo.parse_lists(raw, today, halls=HALLS, muni="ekero")
