# Data spec (version 1)

All generated data lives in `public/data/` and is served as static JSON next to the site.

```
public/data/
  index.json          one entry per hall, points to its schedule file
  sources.json        last run status per adapter
  halls/<hall_id>.json
```

Formal JSON Schemas: [`schema/index.schema.json`](schema/index.schema.json) and
[`schema/hall.schema.json`](schema/hall.schema.json). CI rejects any file that does not validate.

## Conventions

- **Dates** are `YYYY-MM-DD`, **times** are `HH:MM`, both in local Swedish wall-clock time
  (Europe/Stockholm). No offsets, so daylight saving time cannot shift a session.
- **Timestamps** (`fetched_at`, `last_attempt_at`, `generated_at`) are ISO 8601 with offset.
- **`hall_id`** is a stable slug produced by the adapter, prefixed with the municipality,
  e.g. `stockholm-ostermalms-idrottsplats-ishallen`, `tyreso-fontanahallen`.
- **`stick`** is one of `shared` (delad is: part with stick, part without), `with_stick`,
  `no_stick`, `unknown`. Swedish labels belong in the UI, not the data.

## Hall file

```json
{
  "spec_version": 1,
  "hall_id": "stockholm-ostermalms-idrottsplats-ishallen",
  "adapter": "stockholm-motionera",
  "source_name": "Östermalms idrottsplats",
  "source_rink": "Ishallen",
  "status": "ok",
  "error": null,
  "fetched_at": "2026-11-01T06:17:03+01:00",
  "last_attempt_at": "2026-11-01T06:17:03+01:00",
  "source_updated": "2026-10-27T16:03",
  "covers": { "from": "2026-10-26", "to": "2026-11-08" },
  "sessions": [
    {
      "date": "2026-11-06", "start": "12:00", "end": "15:00",
      "stick": "shared",
      "source_url": "https://motionera.stockholm/skridskoakning/",
      "note": null
    }
  ]
}
```

### `covers` — what an empty day means

`covers` is the date range the source actually describes. Inside it, a day with no
sessions means **no public skating**. Outside it, the answer is **unknown** (the source
hasn't published that far yet). The frontend shows these differently: blank vs. "?".

### `status`

| value   | meaning |
|---------|---------|
| `ok`    | the last attempt succeeded; sessions are current |
| `stale` | the last attempt failed; sessions are from `fetched_at` and kept as-is |
| `error` | the last attempt failed and no good data has ever been fetched |

The frontend also treats data as possibly outdated when `fetched_at` is older than 36 hours,
which covers the case where data is held back in a review PR.

### Optional session fields

- `note`: free text from the source that doesn't map to `stick`.
- `changed: true`: the source marks the time as changed after first publication
  (Stockholm uses bold text for this).

## Index file

`index.json` merges each hall file with hand-curated metadata from
[`config/halls.json`](config/halls.json) (display name, short name, coordinates,
link to the facility). A hall that appears in a source but not in the config is still
published with `"curated": false` and `lat`/`lon` set to `null`, and is flagged for review.

## Adapter contract

An adapter is a Python module in `adapters/` registered in `adapters/__init__.py`:

| name | purpose |
|------|---------|
| `NAME` | unique id, e.g. `stockholm-motionera` |
| `MUNICIPALITY` | display name of the municipality |
| `SOURCE_URL` | the page a human can open to verify |
| `fetch() -> dict[str, str]` | download raw documents (the only part touching the network) |
| `parse(raw, today) -> AdapterResult` | pure function: raw documents → halls and sessions |

Rules for `parse`:

1. **Fail loudly on layout changes.** If the page doesn't look as expected, raise
   `ParseError` rather than returning partial or empty data.
2. **Warn on lines you can't read.** Anything that isn't a time and isn't an explicit
   "no times" goes into `warnings`, which sends the run to review.
3. **Report `covers` honestly**: only the range the source really describes.
4. **Never guess times.** No LLM, no fuzzy matching. Unknown is better than wrong.

Every adapter needs a saved HTML fixture in `tests/fixtures/` and tests for its parser.

## Pipeline and checks

1. `python -m pipeline.run` runs every adapter in isolation. A failing source marks its halls
   `stale` and leaves every other source untouched.
2. `python -m pipeline.build_index` writes `index.json`.
3. `python -m pipeline.validate` checks schemas plus semantic rules (end after start,
   hours within 05:00–24:00, sorted, no duplicates, index ↔ files consistent). Failure = nothing is published.
4. `python -m pipeline.anomalies` compares against the published data (git `HEAD`) and flags
   for review when:
   - a parser emitted warnings
   - a hall is new, or missing from `config/halls.json`
   - a hall had sessions in the overlapping future window and now has none
   - more than 50 % of a hall's sessions in the overlapping window changed (at least 4 sessions)
   - an adapter succeeded but produced zero sessions in total

   Week rollovers are not flagged, because only the date range both versions cover is compared.

Clean → committed to `main` and deployed. Review → pushed to the `data-review` branch as a PR.
Source failure → stale markers are published and a single `source-failure` issue is opened.
