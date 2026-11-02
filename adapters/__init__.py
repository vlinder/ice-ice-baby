"""Adapter registry. Add a new source by writing a module and listing it here."""
from . import (
    botkyrka_rbok, danderyd_web, ekero_frigo, haninge_rbok, huddinge_web, nacka_rbok, norrtalje_rbok,
    salem_sportadmin, sigtuna_web, sodertalje_frigo, sollentuna_web, solna_web, stockholm_motionera,
    sundbyberg_web, taby_web, tyreso_rbok, upplands_bro_web, upplands_vasby_web, vallentuna_rbok, varmdo_web,
    vaxholm_actor,
)

ADAPTERS = [
    stockholm_motionera, tyreso_rbok,
    botkyrka_rbok, haninge_rbok, nacka_rbok, norrtalje_rbok, vallentuna_rbok,
    ekero_frigo, sodertalje_frigo,
    danderyd_web, huddinge_web, salem_sportadmin, sigtuna_web, sollentuna_web, solna_web, sundbyberg_web,
    taby_web, upplands_bro_web, upplands_vasby_web, varmdo_web, vaxholm_actor,
]
