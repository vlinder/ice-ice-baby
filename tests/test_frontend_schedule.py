"""The site refetches its data after each scheduled update run, so it must know the schedule."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_frontend_refresh_follows_update_schedule():
    workflow = (ROOT / ".github/workflows/update.yml").read_text()
    crons = re.findall(r'cron:\s*"([^"]+)"', workflow)
    app = (ROOT / "public/app.js").read_text()
    m = re.search(r'const UPDATE_CRON = "([^"]+)"', app)
    assert m, "UPDATE_CRON not found in public/app.js"
    assert crons == [m.group(1)], "update UPDATE_CRON in public/app.js to match update.yml"
