"""Merge curated hall metadata with the generated schedule files into index.json."""
from __future__ import annotations

import sys

from adapters.common import now_local

from .paths import CONFIG_FILE, HALLS_DIR, INDEX_FILE, SOURCES_FILE, SPEC_VERSION, read_json, write_json


def main() -> int:
    config = read_json(CONFIG_FILE)["halls"]
    sources = read_json(SOURCES_FILE) if SOURCES_FILE.exists() else []
    halls = []
    for p in sorted(HALLS_DIR.glob("*.json")):
        d = read_json(p)
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
            "adapter": d["adapter"],
            "status": d["status"],
            "curated": meta is not None,
            "schedule": f"halls/{p.name}",
        })
    halls.sort(key=lambda h: (h["municipality"], h["name"], h["rink"] or ""))
    write_json(INDEX_FILE, {
        "spec_version": SPEC_VERSION,
        "generated_at": now_local().isoformat(),
        "sources": sources,
        "halls": halls,
    })
    print(f"index: {len(halls)} halls")
    return 0


if __name__ == "__main__":
    sys.exit(main())
