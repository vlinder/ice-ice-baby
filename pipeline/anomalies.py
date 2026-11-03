"""Compare freshly generated data with the last published version (git HEAD).

Findings:
  review   – the new data looks suspicious; a human should look before it's published
  failures – a source could not be fetched/parsed; its halls are already marked stale
  skipped  – parts of a source the parser could not read (informational: the rest is
             published, the halls carry "problems", and pipeline.problem_report opens issues)

Writes anomalies.json and anomalies.md, and prints a summary.
Exit code: 0 = nothing to review, 3 = review needed. Failures alone exit 0
(stale markers are safe to publish) but are reported for the issue tracker.

Usage:  python -m pipeline.anomalies [--baseline-ref HEAD] [--today YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date

from adapters.common import today_local

from .paths import CONFIG_FILE, HALLS_DIR, ROOT, SOURCES_FILE, read_json, write_json

MIN_SESSIONS_FOR_CHANGE_RULE = 4
MAX_CHANGED_SHARE = 0.5


def baseline(ref: str, rel: str) -> dict | None:
    try:
        out = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=ROOT, capture_output=True, check=True, text=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def key(s: dict) -> tuple:
    return (s["date"], s["start"], s["end"], s["stick"])


def compare_hall(old: dict | None, new: dict, today: str) -> list[str]:
    hid = new["hall_id"]
    if old is None or new["status"] != "ok":
        return []
    lo = max(old["covers"]["from"], new["covers"]["from"], today)
    hi = min(old["covers"]["to"], new["covers"]["to"])
    if lo > hi:
        return []  # no overlapping future window, e.g. week rollover
    in_win = lambda s: lo <= s["date"] <= hi  # noqa: E731
    o = {key(s) for s in old["sessions"] if in_win(s)}
    n = {key(s) for s in new["sessions"] if in_win(s)}
    found = []
    if o and not n:
        found.append(f"`{hid}`: had {len(o)} sessions between {lo} and {hi}, now has none")
    elif len(o | n) >= MIN_SESSIONS_FOR_CHANGE_RULE:
        changed = len(o ^ n) / len(o | n)
        if changed > MAX_CHANGED_SHARE:
            found.append(f"`{hid}`: {changed:.0%} of sessions between {lo} and {hi} changed "
                         f"({len(n - o)} added, {len(o - n)} removed)")
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline-ref", default="HEAD")
    ap.add_argument("--today")
    args = ap.parse_args(argv)
    today = (date.fromisoformat(args.today) if args.today else today_local()).isoformat()

    config = read_json(CONFIG_FILE)["halls"]
    review: list[str] = []
    failures: list[str] = []
    notes: list[str] = []

    for src in (read_json(SOURCES_FILE) if SOURCES_FILE.exists() else []):
        if src["status"] != "ok":
            failures.append(f"`{src['adapter']}` failed: {src.get('error')}")
        # Parser warnings do not hold data back: the unreadable parts were skipped, the halls
        # carry "problems", and pipeline.problem_report opens an issue for them.
        notes += [f"`{src['adapter']}`: skipped {w}" for w in src.get("warnings") or []]

    produced = {}
    for p in sorted(HALLS_DIR.glob("*.json")):
        new = read_json(p)
        produced.setdefault(new["adapter"], 0)
        produced[new["adapter"]] += len(new["sessions"])
        hid = new["hall_id"]
        rel = p.relative_to(ROOT).as_posix()
        old = baseline(args.baseline_ref, rel)
        if old is None:
            review.append(f"`{hid}`: new hall (first time seen)")
        if hid not in config:
            review.append(f"`{hid}`: missing from config/halls.json (no curated name/coordinates)")
        review += compare_hall(old, new, today)

    sources = {s["adapter"]: s for s in (read_json(SOURCES_FILE) if SOURCES_FILE.exists() else [])}
    for adapter, n in produced.items():
        src = sources.get(adapter) or {}
        # An adapter that recognised an explicit "no times" message is not suspicious.
        if src.get("status") == "ok" and n == 0 and not src.get("empty_reason"):
            review.append(f"`{adapter}`: produced zero sessions across all halls (off-season, or a broken parser?)")

    report = {"today": today, "review": review, "failures": failures, "skipped": notes}
    write_json(ROOT / "anomalies.json", report)
    md = ["## Data check", ""]
    md += ["### Needs review", *[f"- {r}" for r in review], ""] if review else ["Nothing suspicious in the new data.", ""]
    if failures:
        md += ["### Source failures (halls marked stale)", *[f"- {f}" for f in failures], ""]
    if notes:
        md += ["### Skipped parts of sources (published anyway, see parser-problem issues)",
               *[f"- {n}" for n in notes], ""]
    (ROOT / "anomalies.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    return 3 if review else 0


if __name__ == "__main__":
    sys.exit(main())
