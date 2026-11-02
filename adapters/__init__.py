"""Adapter registry. Add a new source by writing a module and listing it here."""
from . import (
    botkyrka_rbok, ekero_frigo, haninge_rbok, nacka_rbok, norrtalje_rbok, sodertalje_frigo,
    stockholm_motionera, tyreso_rbok, vallentuna_rbok,
)

ADAPTERS = [
    stockholm_motionera, tyreso_rbok,
    botkyrka_rbok, haninge_rbok, nacka_rbok, norrtalje_rbok, vallentuna_rbok,
    ekero_frigo, sodertalje_frigo,
]
