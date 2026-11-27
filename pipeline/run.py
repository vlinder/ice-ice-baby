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
from adapters.common import AdapterResult, HallResult, now_local, today_local

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
                aliases: dict[str, str] | None = None) -> dict:
    aliases = aliases or {}
    previous = existing_halls(adapter.NAME)
    # Files written under an id that has since become an alias would otherwise linger forever.
    for hid in [h for h in previous if h in aliases]:
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
        result = merge_aliases(adapter.parse(raw, today), aliases)
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
    for adapter in ADAPTERS:
        if args.only and adapter.NAME != args.only:
            continue
        old_sources[adapter.NAME] = run_adapter(adapter, today, stamp, corrections, aliases)
    write_json(SOURCES_FILE, [old_sources[a.NAME] for a in ADAPTERS if a.NAME in old_sources])
    # Never fail the run because a source failed: stale data is handled downstream.
    return 0


if __name__ == "__main__":
    sys.exit(main())
