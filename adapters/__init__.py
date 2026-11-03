"""Adapter registry. Add a new source by writing a module and listing it here.

Stockholm County has 26 municipalities; Nykvarn is missing because it publishes
no times (natural ice only).
"""
from . import (
    botkyrka_rbok, danderyd_web, ekero_interbook, haninge_rbok, huddinge_web, jarfalla_web, lidingo_web,
    nacka_rbok, norrtalje_rbok, nynashamn_web, osteraker_web, salem_sportadmin, sigtuna_web,
    sodertalje_frigo, sollentuna_web, solna_web, stockholm_motionera, sundbyberg_web, taby_web,
    tyreso_rbok, upplands_bro_web, upplands_vasby_web, vallentuna_rbok, varmdo_web, vaxholm_actor,
)

ADAPTERS = [
    stockholm_motionera, tyreso_rbok,
    # Rbok information boards (adapters/rbok.py)
    botkyrka_rbok, haninge_rbok, nacka_rbok, norrtalje_rbok, vallentuna_rbok,
    # FRI-GO booking lists (adapters/frigo.py)
    sodertalje_frigo,
    # Other systems
    ekero_interbook, salem_sportadmin, vaxholm_actor,
    # The municipalities' own pages
    danderyd_web, huddinge_web, jarfalla_web, lidingo_web, nynashamn_web, osteraker_web, sigtuna_web,
    sollentuna_web, solna_web, sundbyberg_web, taby_web, upplands_bro_web, upplands_vasby_web, varmdo_web,
]
