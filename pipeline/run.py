"""Run every adapter and write one schedule file per hall.

Each adapter is isolated: if one source fails, its halls keep their last good
sessions and are marked "stale"; every other source is unaffected.

Usage:  python -m pipeline.run [--only ADAPTER] [--today YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date

from adapters import ADAPTERS
from adapters.common import now_local, today_local

from .paths import HALLS_DIR, SOURCES_FILE, SPEC_VERSION, read_json, write_json


def existing_halls(adapter_name: str) -> dict[str, dict]:
    out = {}
    for p in HALLS_DIR.glob("*.json"):
        d = read_json(p)
        if d.get("adapter") == adapter_name:
            out[d["hall_id"]] = d
    return out


def run_adapter(adapter, today: date, stamp: str) -> dict:
    previous = existing_halls(adapter.NAME)
    source = {
        "adapter": adapter.NAME,
        "municipality": adapter.MUNICIPALITY,
        "source_url": adapter.SOURCE_URL,
        "last_attempt_at": stamp,
    }
    try:
        result = adapter.parse(adapter.fetch(), today)
    except Exception as exc:  # noqa: BLE001 – any failure must degrade gracefully
        msg = f"{type(exc).__name__}: {exc}"
        print(f"[{adapter.NAME}] FAILED: {msg}", file=sys.stderr)
        traceback.print_exc()
        for hid, d in previous.items():
            d["status"] = "stale" if d.get("fetched_at") else "error"
            d["error"] = msg
            d["last_attempt_at"] = stamp
            write_json(HALLS_DIR / f"{hid}.json", d)
        prev_src = _previous_source(adapter.NAME)
        return {**source, "status": "error", "error": msg,
                "fetched_at": prev_src.get("fetched_at") if prev_src else None, "warnings": []}

    for w in result.warnings:
        print(f"[{adapter.NAME}] warning: {w}", file=sys.stderr)

    hall_ids = set(result.halls) | set(previous)
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
            "fetched_at": stamp,
            "last_attempt_at": stamp,
            "source_updated": result.source_updated,
            # A hall absent from the source still gets the covered range: it had no public times.
            "covers": {"from": (hall and hall.covers_from) or result.covers_from,
                       "to": (hall and hall.covers_to) or result.covers_to},
            "sessions": sessions,
        })
    n = sum(len(h.sessions) for h in result.halls.values())
    print(f"[{adapter.NAME}] ok: {len(result.halls)} halls, {n} sessions, covers {result.covers_from}..{result.covers_to}")
    return {**source, "status": "ok", "error": None, "fetched_at": stamp, "warnings": result.warnings,
            "empty_reason": result.empty_reason}


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
    for adapter in ADAPTERS:
        if args.only and adapter.NAME != args.only:
            continue
        old_sources[adapter.NAME] = run_adapter(adapter, today, stamp)
    write_json(SOURCES_FILE, [old_sources[a.NAME] for a in ADAPTERS if a.NAME in old_sources])
    # Never fail the run because a source failed: stale data is handled downstream.
    return 0


if __name__ == "__main__":
    sys.exit(main())
