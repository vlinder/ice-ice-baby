"""Run every adapter and write one schedule file per hall.

Each adapter is isolated: if one source fails, its halls keep their last good
sessions and are marked "stale"; every other source is unaffected.

Run timestamps (fetched_at, last_attempt_at) are recorded per source in sources.json,
not in the hall files, so a hall file only changes when its schedule does.

Usage:  python -m pipeline.run [--only ADAPTER] [--today YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date

from adapters import ADAPTERS
from adapters.common import (
    STICK_SHARED, STICK_UNKNOWN, STICK_WITH, STICK_WITHOUT, AdapterResult, HallResult, Session,
    now_local, today_local,
)

from .paths import CONFIG_FILE, HALLS_DIR, SOURCES_FILE, SPEC_VERSION, read_json, write_json
from .problems import apply_corrections, attribute, load_corrections


def existing_halls(adapter_name: str) -> dict[str, dict]:
    out = {}
    for p in HALLS_DIR.glob("*.json"):
        d = read_json(p)
        if d.get("adapter") == adapter_name:
            out[d["hall_id"]] = d
    return out


def load_aliases(config_file=None) -> dict[str, str]:
    """Map each alias in config/halls.json to the hall id it belongs to.

    A source sometimes names the same rink differently from one week to the next
    ("Uteisen – Halvmånar" / "Halvcirkelformade isar ..."). Each wording gives its own
    hall id, so the config lists the other wordings' ids as "aliases" of one hall.
    """
    config = read_json(config_file or CONFIG_FILE)["halls"]
    out: dict[str, str] = {}
    for hid, meta in config.items():
        for alias in meta.get("aliases", []):
            if alias in config:
                raise ValueError(f"config/halls.json: alias {alias!r} of {hid!r} is also a hall of its own")
            if alias in out:
                raise ValueError(f"config/halls.json: alias {alias!r} is listed for both {out[alias]!r} and {hid!r}")
            out[alias] = hid
    return out


def load_parts(config_file=None) -> dict[str, tuple[str, str, int]]:
    """Map each source hall id listed under a hall's "parts" to (hall id, part name, order).

    Some ice is one surface shared by parts with their own times and stick rules, e.g. the
    outdoor ice at Östermalms IP: "Rundbana och innerplan" and "Halvmånarna". The source lists
    each part as a rink of its own; the config folds them into one hall (see merge_parts).
    """
    config = read_json(config_file or CONFIG_FILE)["halls"]
    aliases = {a for meta in config.values() for a in meta.get("aliases", [])}
    out: dict[str, tuple[str, str, int]] = {}
    for hid, meta in config.items():
        for order, part in enumerate(meta.get("parts", [])):
            for pid in part["ids"]:
                if pid in config or pid in aliases:
                    raise ValueError(f"config/halls.json: part id {pid!r} of {hid!r} is also a hall or an alias")
                if pid in out:
                    raise ValueError(f"config/halls.json: part id {pid!r} is listed twice")
                out[pid] = (hid, part["name"], order)
    return out


STICK_WORDS = {STICK_WITHOUT: "utan klubba", STICK_WITH: "med klubba", STICK_SHARED: "delad is"}


def _combined_stick(sticks: set[str]) -> str:
    if len(sticks) == 1:
        return next(iter(sticks))
    if STICK_UNKNOWN in sticks:
        return STICK_UNKNOWN
    return STICK_SHARED  # parts with different rules at the same time


def _share_note(open_parts: list[tuple[int, str, str, str | None]], n_parts: int) -> str:
    """'Rundbana och innerplan' for one part (its rule is the session's stick), 'Hela isen'
    when every part is open under the same rule, or 'Rundbana och innerplan utan klubba,
    halvmånarna med klubba' for several parts with different rules."""
    if len(open_parts) == n_parts > 1 and len({(st, nt) for _, _, st, nt in open_parts}) == 1:
        note = open_parts[0][3]
        return f"Hela isen ({note})" if note else "Hela isen"
    words = []
    for _, name, stick, note in open_parts:
        w = name if len(open_parts) == 1 else " ".join(x for x in (name, STICK_WORDS.get(stick)) if x)
        words.append(f"{w} ({note})" if note else w)
    text = ", ".join(words)
    return text[:1].upper() + text[1:]


def share_sessions(parts: list[tuple[int, str, list[Session]]], n_parts: int | None = None) -> list[Session]:
    """Sessions of one shared ice from its parts' sessions: (order, name, sessions) each.
    n_parts is how many parts the ice has (default: as many as given).

    A session starts or ends wherever the set of open parts, or their stick rules, change."""
    by_date: dict[str, list[tuple[int, str, Session]]] = {}
    for order, name, sessions in parts:
        for s in sessions:
            by_date.setdefault(s.date, []).append((order, name, s))
    n_parts = n_parts or len({order for order, _, _ in parts})
    out: list[Session] = []
    for day, items in sorted(by_date.items()):
        cuts = sorted({t for _, _, s in items for t in (s.start, s.end)})
        current: Session | None = None
        key = None
        for a, b in zip(cuts, cuts[1:]):
            open_parts = sorted({(o, n, s.stick, s.note) for o, n, s in items if s.start <= a and b <= s.end})
            if not open_parts:
                current, key = None, None
                continue
            k = tuple(open_parts)
            covering = [s for _, _, s in items if s.start <= a and b <= s.end]
            changed = any(s.changed for s in covering)
            tags = sorted({t for s in covering for t in s.tags})
            if current is not None and k == key and current.end == a:
                current.end = b
                current.changed = current.changed or changed
                continue
            current = Session(date=day, start=a, end=b,
                              stick=_combined_stick({st for _, _, st, _ in open_parts}),
                              source_url=items[0][2].source_url, note=_share_note(open_parts, n_parts),
                              changed=changed, tags=tags)
            key = k
            out.append(current)
    return out


def merge_parts(result: AdapterResult, parts: dict[str, tuple[str, str, int]]) -> AdapterResult:
    """Fold the halls listed as parts of a shared ice into that one hall."""
    groups: dict[str, list[tuple[int, str, HallResult]]] = {}
    halls: dict[str, HallResult] = {}
    for hid, hall in result.halls.items():
        if hid in parts:
            target, name, order = parts[hid]
            groups.setdefault(target, []).append((order, name, hall))
        else:
            halls[hid] = hall
    for target, members in groups.items():
        first = min(members, key=lambda m: m[0])[2]
        # A part can come in under several source ids (one per wording): one list per part.
        by_part: dict[tuple[int, str], list[Session]] = {}
        for o, n, h in members:
            by_part.setdefault((o, n), []).extend(h.sessions)
        n_parts = len({o for t, _, o in parts.values() if t == target})
        merged = HallResult(
            hall_id=target, name=first.name, rink=None,
            sessions=share_sessions([(o, n, ss) for (o, n), ss in sorted(by_part.items())], n_parts),
            covers_from=_bounds([h.covers_from for _, _, h in members], min),
            covers_to=_bounds([h.covers_to for _, _, h in members], max),
        )
        if target in halls:
            raise ValueError(f"{target} is both a hall in the source and made of parts")
        halls[target] = merged
    warnings = []
    for w in result.warnings:
        for pid in sorted(parts, key=len, reverse=True):
            if w == pid or w.startswith(pid + ":") or w.startswith(pid + " "):
                w = parts[pid][0] + w[len(pid):]
                break
        warnings.append(w)
    result.halls, result.warnings = halls, warnings
    return result


def _bounds(values: list[str | None], pick) -> str | None:
    # None means "the adapter-wide range", which a merged hall must then use as well.
    return None if None in values else pick(values)


def merge_aliases(result: AdapterResult, aliases: dict[str, str]) -> AdapterResult:
    """Fold halls whose id is an alias into the hall they are an alias of."""
    groups: dict[str, list[HallResult]] = {}
    for hid, hall in result.halls.items():
        groups.setdefault(aliases.get(hid, hid), []).append(hall)
    halls = {}
    for hid, parts in groups.items():
        # Prefer the hall's own wording for source_name/source_rink when the source uses it.
        first = next((h for h in parts if h.hall_id == hid), parts[0])
        sessions = []
        for h in parts:
            sessions += [s for s in h.sessions if s not in sessions]
        halls[hid] = HallResult(
            hall_id=hid, name=first.name, rink=first.rink, sessions=sessions,
            covers_from=_bounds([h.covers_from for h in parts], min),
            covers_to=_bounds([h.covers_to for h in parts], max),
        )
    # Warnings start with the hall id; rewrite alias ids so problems land on the right hall.
    warnings = []
    for w in result.warnings:
        for alias in sorted(aliases, key=len, reverse=True):
            if w == alias or w.startswith(alias + ":") or w.startswith(alias + " "):
                w = aliases[alias] + w[len(alias):]
                break
        warnings.append(w)
    result.halls, result.warnings = halls, warnings
    return result


def run_adapter(adapter, today: date, stamp: str, corrections: list[dict] | None = None,
                aliases: dict[str, str] | None = None,
                parts: dict[str, tuple[str, str, int]] | None = None) -> dict:
    aliases = aliases or {}
    parts = parts or {}
    previous = existing_halls(adapter.NAME)
    # Files written under an id that has since become an alias or a part would otherwise linger forever.
    for hid in [h for h in previous if h in aliases or h in parts]:
        (HALLS_DIR / f"{hid}.json").unlink()
        del previous[hid]
    source = {
        "adapter": adapter.NAME,
        "municipality": adapter.MUNICIPALITY,
        "source_url": adapter.SOURCE_URL,
        "last_attempt_at": stamp,
    }
    used: list[dict] = []
    unused: list[dict] = []
    try:
        raw, used, unused = apply_corrections(adapter.NAME, adapter.fetch(), corrections or [])
        result = merge_parts(merge_aliases(adapter.parse(raw, today), aliases), parts)
    except Exception as exc:  # noqa: BLE001 – any failure must degrade gracefully
        msg = f"{type(exc).__name__}: {exc}"
        print(f"[{adapter.NAME}] FAILED: {msg}", file=sys.stderr)
        traceback.print_exc()
        for hid, d in previous.items():
            # "error" only if this hall has never had good data; otherwise keep its sessions.
            d["status"] = "error" if d.get("status") == "error" else "stale"
            d["error"] = msg
            write_json(HALLS_DIR / f"{hid}.json", d)
        prev_src = _previous_source(adapter.NAME)
        return {**source, "status": "error", "error": msg,
                "fetched_at": prev_src.get("fetched_at") if prev_src else None, "warnings": []}

    for w in result.warnings:
        print(f"[{adapter.NAME}] warning: {w}", file=sys.stderr)

    hall_ids = set(result.halls) | set(previous)
    # Parts of the source that could not be read were skipped; record that on the halls.
    problems = attribute(result.warnings, sorted(result.halls))
    for hid in sorted(hall_ids):
        hall = result.halls.get(hid)
        prev = previous.get(hid, {})
        sessions = sorted((s.to_json() for s in hall.sessions), key=lambda s: (s["date"], s["start"], s["end"])) if hall else []
        write_json(HALLS_DIR / f"{hid}.json", {
            "spec_version": SPEC_VERSION,
            "hall_id": hid,
            "adapter": adapter.NAME,
            "source_name": hall.name if hall else prev.get("source_name", hid),
            "source_rink": hall.rink if hall else prev.get("source_rink"),
            "status": "ok",
            "error": None,
            "source_updated": result.source_updated,
            # A hall absent from the source still gets the covered range: it had no public times.
            "covers": {"from": (hall and hall.covers_from) or result.covers_from,
                       "to": (hall and hall.covers_to) or result.covers_to},
            "sessions": sessions,
            "problems": problems.get(hid, []),
        })
    n = sum(len(h.sessions) for h in result.halls.values())
    print(f"[{adapter.NAME}] ok: {len(result.halls)} halls, {n} sessions, covers {result.covers_from}..{result.covers_to}")
    return {**source, "status": "ok", "error": None, "fetched_at": stamp, "warnings": result.warnings,
            "empty_reason": result.empty_reason,
            "corrections_used": [c["text"] for c in used],
            "corrections_unused": [c["text"] for c in unused]}


def _previous_source(name: str) -> dict | None:
    if not SOURCES_FILE.exists():
        return None
    return next((s for s in read_json(SOURCES_FILE) if s["adapter"] == name), None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="run a single adapter by NAME")
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD), for testing")
    args = ap.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else today_local()
    stamp = now_local().isoformat()
    HALLS_DIR.mkdir(parents=True, exist_ok=True)

    old_sources = {s["adapter"]: s for s in read_json(SOURCES_FILE)} if SOURCES_FILE.exists() else {}
    corrections = load_corrections()
    aliases = load_aliases()
    parts = load_parts()
    for adapter in ADAPTERS:
        if args.only and adapter.NAME != args.only:
            continue
        old_sources[adapter.NAME] = run_adapter(adapter, today, stamp, corrections, aliases, parts)
    write_json(SOURCES_FILE, [old_sources[a.NAME] for a in ADAPTERS if a.NAME in old_sources])
    # Never fail the run because a source failed: stale data is handled downstream.
    return 0


if __name__ == "__main__":
    sys.exit(main())
