# Allmänhetens åkning

Automatically updated public-skating times for the ice rinks in **Stockholms stad** and
**Tyresö kommun**, published as a static site with a list per day, a timeline, a week
overview and a map. Every time links back to the page it was read from.

- **No API keys, no LLM at runtime.** Plain Python parsers run on free GitHub Actions runners.
- **Free hosting** on GitHub Pages.
- **Fails safe.** Broken sources are marked stale instead of showing wrong times, and
  suspicious changes wait in a pull request for a human.

## How it works

```
cron (3×/day) ─► adapters ─► public/data/halls/*.json ─► validate ─► compare with published
                                                                        │
                                   ┌────────────────────────────────────┼─────────────────────┐
                               looks normal                     looks suspicious        a source failed
                                   │                                    │                     │
                          commit to main + deploy              PR on data-review      issue "source-failure"
```

| Source | Adapter | Notes |
|---|---|---|
| [motionera.stockholm/skridskoakning](https://motionera.stockholm/skridskoakning/) | `adapters/stockholm_motionera.py` | Weekly lists, usually current + next week |
| [Tyresö: Rbok information board](https://tyreso.rbok.se/informationstavla?id=fbe27841-479e-4387-8e40-9b6a158436c4) | `adapters/tyreso_rbok.py` | Linked from tyreso.se; about two weeks ahead, includes holiday times |

See [SPEC.md](SPEC.md) for the data format, the adapter contract and the review rules.

## Setup (once)

1. Create an **empty public repository** on GitHub (public = free Actions minutes and Pages).
   Use `main` as the default branch.
2. Push this code:
   ```bash
   git remote add origin git@github.com:<you>/<repo>.git
   git push -u origin main
   ```
3. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
4. **Settings → Actions → General → Workflow permissions:** choose *Read and write permissions*
   and tick *Allow GitHub Actions to create and approve pull requests* (needed for review PRs).
5. **Actions → Update schedules → Run workflow** to fetch fresh data and deploy.
   The site appears at `https://<you>.github.io/<repo>/`.

After that, the schedule runs by itself.

## Day-to-day

- **Nothing to do** while builds are green.
- **A PR titled "Schemadata behöver granskas"** means the new data looks odd. Read the
  report in the PR, compare with the source, and merge if correct (or fix the adapter).
- **An issue "En källa kunde inte läsas"** means a source failed. Its halls show
  "kan vara inaktuell" on the site until the next good run. If it keeps failing, the
  page layout has probably changed: save the new HTML as a fixture, fix the parser,
  and close the issue.
- **A red "Validate" step** means the output broke the spec; nothing was published.

## Local development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q                       # parser tests against saved fixtures
python -m pipeline.run                    # fetch live data
python -m pipeline.build_index
python -m pipeline.validate
python -m pipeline.anomalies              # compare with git HEAD
python -m http.server -d public 8000      # open http://localhost:8000
```

## Adding a hall or a municipality

- **New hall in an existing source:** it is picked up automatically and flagged for review.
  Add its name, short name and coordinates to `config/halls.json`.
- **New municipality:** write `adapters/<name>.py` following the contract in SPEC.md,
  save a fixture, add tests, register it in `adapters/__init__.py`, and add its halls to
  `config/halls.json`.

## Good to know

- GitHub may pause scheduled workflows in repositories with no activity for 60 days.
  If updates stop, re-enable the workflow under the Actions tab.
- Map tiles come from OpenStreetMap under their
  [tile usage policy](https://operations.osmfoundation.org/policies/tiles/). This is fine for
  a low-traffic site; switch tile provider if traffic grows.
- Coordinates in `config/halls.json` are approximate.
- This is an unofficial compilation. Municipalities can change times at short notice.
