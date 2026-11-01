from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "public" / "data"
HALLS_DIR = DATA / "halls"
SOURCES_FILE = DATA / "sources.json"
INDEX_FILE = DATA / "index.json"
CONFIG_FILE = ROOT / "config" / "halls.json"
SCHEMA_DIR = ROOT / "schema"
SPEC_VERSION = 1


def read_json(path):
    import json
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
