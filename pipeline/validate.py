"""Validate generated data against the JSON Schemas plus semantic rules.

Exit code 1 means the data must not be published.
"""
from __future__ import annotations

import sys

from jsonschema import Draft202012Validator, FormatChecker

from .paths import DATA, HALLS_DIR, INDEX_FILE, SCHEMA_DIR, read_json

EARLIEST, LATEST = "05:00", "24:00"


def schema_errors(schema_name: str, data) -> list[str]:
    v = Draft202012Validator(read_json(SCHEMA_DIR / schema_name), format_checker=FormatChecker())
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in v.iter_errors(data)]


def semantic_errors(hall: dict) -> list[str]:
    errs = []
    cov = hall["covers"]
    if cov["from"] > cov["to"]:
        errs.append(f"covers.from {cov['from']} is after covers.to {cov['to']}")
    seen = set()
    prev_key = None
    for s in hall["sessions"]:
        key = (s["date"], s["start"], s["end"], s["stick"])
        if s["end"] <= s["start"]:
            errs.append(f"{s['date']} {s['start']}-{s['end']}: end is not after start")
        if s["start"] < EARLIEST or s["end"] > LATEST:
            errs.append(f"{s['date']} {s['start']}-{s['end']}: outside sane hours {EARLIEST}-{LATEST}")
        if key in seen:
            errs.append(f"duplicate session {key}")
        if prev_key and key[:3] < prev_key[:3]:
            errs.append("sessions are not sorted by date/start/end")
        seen.add(key)
        prev_key = key
    return errs


def main() -> int:
    problems: list[str] = []
    if not INDEX_FILE.exists():
        print("index.json missing", file=sys.stderr)
        return 1
    index = read_json(INDEX_FILE)
    problems += [f"index.json: {e}" for e in schema_errors("index.schema.json", index)]
    listed = set()
    for h in index.get("halls", []):
        path = DATA / h["schedule"]
        listed.add(path.name)
        if not path.exists():
            problems.append(f"index.json: {h['id']} points to missing {h['schedule']}")
            continue
        hall = read_json(path)
        errs = schema_errors("hall.schema.json", hall)
        if not errs:
            errs = semantic_errors(hall)
            if hall["hall_id"] != h["id"]:
                errs.append(f"hall_id {hall['hall_id']} does not match index id {h['id']}")
        problems += [f"{h['schedule']}: {e}" for e in errs]
    for p in HALLS_DIR.glob("*.json"):
        if p.name not in listed:
            problems.append(f"{p.name} exists but is not listed in index.json")

    for p in problems:
        print(f"INVALID {p}", file=sys.stderr)
    print("validation: " + ("FAILED" if problems else "ok"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
