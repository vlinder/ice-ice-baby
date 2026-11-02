"""Parser problems and manual corrections.

Problems
--------
When an adapter cannot read part of its source (a typo like "11-00–12.50", an
unknown heading), it skips that part and adds a warning string. The run still
publishes everything else. Each warning is attached to the hall it concerns as
a "problem", so the site can show that the hall's times may be incomplete.

Convention: a warning that starts with a hall id ("huddinge-visattrahallen: …")
belongs to that hall. Other warnings belong to every hall of the adapter.
The first quoted string in the warning (Python repr, as in f"{line!r}") is
taken as the source text that could not be read.

Corrections
-----------
config/corrections.json holds typo fixes a human has confirmed:

    {"adapter": "huddinge-web", "text": "11-00–12.50", "read_as": "11.00–12.50",
     "note": "...", "added": "2026-11-02"}

Before parsing, `text` is replaced by `read_as` in the fetched documents (also
in HTML-escaped form). A correction only applies while the source still shows
that exact text, so it stops by itself when the municipality fixes the page.
Corrections that matched nothing are reported so they can be removed.
"""
from __future__ import annotations

import ast
import html
import re

from .paths import ROOT, read_json

CORRECTIONS_FILE = ROOT / "config" / "corrections.json"

_QUOTED = re.compile(r"""'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*\"""")


def load_corrections() -> list[dict]:
    if not CORRECTIONS_FILE.exists():
        return []
    data = read_json(CORRECTIONS_FILE)
    return data["corrections"] if isinstance(data, dict) else data


def _variants(text: str) -> list:
    """Ways a piece of text can be written in a raw document (plain, HTML-escaped, dash entities)."""
    forms = [
        lambda s: s,
        lambda s: html.escape(s, quote=False),
        lambda s: s.replace("–", "&ndash;").replace("—", "&mdash;"),
        lambda s: s.replace("–", "&#8211;").replace("—", "&#8212;"),
    ]
    seen, out = set(), []
    for f in forms:
        if f(text) not in seen:
            seen.add(f(text))
            out.append(f)
    return out


def apply_corrections(adapter: str, raw: dict[str, str], corrections: list[dict]
                      ) -> tuple[dict[str, str], list[dict], list[dict]]:
    """Return (corrected raw documents, corrections used, corrections unused) for one adapter."""
    used, unused = [], []
    raw = dict(raw)
    for c in corrections:
        if c.get("adapter") != adapter:
            continue
        hits = 0
        for f in _variants(c["text"]):
            src, dst = f(c["text"]), f(c["read_as"])
            for k, doc in raw.items():
                n = doc.count(src)
                if n:
                    raw[k] = doc.replace(src, dst)
                    hits += n
        (used if hits else unused).append(c)
    return raw, used, unused


def source_text(warning: str) -> str | None:
    """The first quoted string in a warning, if it is short enough to show people."""
    for m in _QUOTED.finditer(warning):
        try:
            value = ast.literal_eval(m.group(0))
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, str) and 0 < len(value) <= 120:
            return value
    return None


def attribute(warnings: list[str], hall_ids: list[str]) -> dict[str, list[dict]]:
    """Map each hall id to the problems that concern it."""
    out: dict[str, list[dict]] = {h: [] for h in hall_ids}
    # Longest ids first, so "x-ishall-a" is not taken for "x-ishall".
    ids = sorted(hall_ids, key=len, reverse=True)
    for w in warnings:
        problem = {"message": w, "source_text": source_text(w)}
        owner = next((h for h in ids if w == h or w.startswith(h + ":") or w.startswith(h + " ")), None)
        for h in ([owner] if owner else hall_ids):
            out[h].append(problem)
    return out
