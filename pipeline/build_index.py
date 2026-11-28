"""Merge curated hall metadata with the generated schedule files into index.json.

Each hall entry carries the hall file's `version` (a hash of its bytes) and `changed_at`
(when those bytes last changed), so the site only fetches hall files that changed.
`changed_at` is carried over from the previous index.json while the version is the same.
"""
from __future__ import annotations

import hashlib
import sys

from adapters.common import now_local

from .paths import CONFIG_FILE, HALLS_DIR, INDEX_FILE, SOURCES_FILE, SPEC_VERSION, read_json, write_json


def file_version(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _previous_versions() -> dict[str, tuple[str, str]]:
    """hall id -> (version, changed_at) from the index.json being replaced."""
    if not INDEX_FILE.exists():
        return {}
    try:
        old = read_json(INDEX_FILE)
    except ValueError:
        return {}
    return {h["id"]: (h["version"], h["changed_at"]) for h in old.get("halls", [])
            if h.get("version") and h.get("changed_at")}


def main() -> int:
    config = read_json(CONFIG_FILE)["halls"]
    sources = read_json(SOURCES_FILE) if SOURCES_FILE.exists() else []
    previous = _previous_versions()
    now = now_local().isoformat()
    halls = []
    for p in sorted(HALLS_DIR.glob("*.json")):
        d = read_json(p)
        version = file_version(p)
        old_version, old_changed = previous.get(d["hall_id"], (None, None))
        hid = d["hall_id"]
        meta = config.get(hid)
        if meta is None:
            print(f"warning: {hid} is not in config/halls.json – add name and coordinates", file=sys.stderr)
        halls.append({
            "id": hid,
            "name": (meta or {}).get("name", d.get("source_name", hid)),
            "short_name": (meta or {}).get("short_name", d.get("source_name", hid)),
            "rink": (meta or {}).get("rink", d.get("source_rink")),
            "municipality": (meta or {}).get("municipality") or next(
                (s["municipality"] for s in sources if s["adapter"] == d["adapter"]), "Okänd"),
            "lat": (meta or {}).get("lat"),
            "lon": (meta or {}).get("lon"),
            "info_url": (meta or {}).get("info_url"),
            "outdoor": bool((meta or {}).get("outdoor", False)),
            "adapter": d["adapter"],
            "status": d["status"],
            "curated": meta is not None,
            "schedule": f"halls/{p.name}",
            "version": version,
            "changed_at": old_changed if old_version == version else now,
        })
    halls.sort(key=lambda h: (h["municipality"], h["name"], h["rink"] or ""))
    write_json(INDEX_FILE, {
        "spec_version": SPEC_VERSION,
        "generated_at": now,
        "sources": sources,
        "halls": halls,
    })
    print(f"index: {len(halls)} halls")
    return 0


if __name__ == "__main__":
    sys.exit(main())
