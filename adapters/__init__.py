"""Adapter registry. Add a new source by writing a module and listing it here."""
from . import (
    botkyrka_rbok, ekero_frigo, haninge_rbok, nacka_rbok, norrtalje_rbok, salem_sportadmin, sodertalje_frigo,
    stockholm_motionera, tyreso_rbok, upplands_vasby_web, vallentuna_rbok, varmdo_web, vaxholm_actor,
)

ADAPTERS = [
    stockholm_motionera, tyreso_rbok,
    botkyrka_rbok, haninge_rbok, nacka_rbok, norrtalje_rbok, vallentuna_rbok,
    ekero_frigo, sodertalje_frigo,
    salem_sportadmin, upplands_vasby_web, varmdo_web, vaxholm_actor,
]
