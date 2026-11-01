"""Adapter registry. Add a new source by writing a module and listing it here."""
from . import stockholm_motionera, tyreso_rbok

ADAPTERS = [stockholm_motionera, tyreso_rbok]
