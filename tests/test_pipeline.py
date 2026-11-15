from pipeline.anomalies import compare_hall
from pipeline.validate import semantic_errors


def hall(sessions, cov=("2026-11-02", "2026-11-08"), status="ok"):
    return {"hall_id": "x", "status": status, "covers": {"from": cov[0], "to": cov[1]},
            "sessions": [{"date": d, "start": s, "end": e, "stick": "shared"} for d, s, e in sessions]}


def test_semantic_rules():
    assert semantic_errors(hall([("2026-11-02", "08:00", "09:00")])) == []
    assert semantic_errors(hall([("2026-11-02", "10:00", "09:00")]))
    assert semantic_errors(hall([("2026-11-02", "03:00", "04:00")]))
    assert semantic_errors(hall([("2026-11-03", "08:00", "09:00"), ("2026-11-02", "08:00", "09:00")]))


def test_drop_to_zero_is_flagged():
    old = hall([("2026-11-03", "08:00", "09:00"), ("2026-11-04", "08:00", "09:00")])
    assert compare_hall(old, hall([]), "2026-11-02")


def test_big_change_is_flagged_small_change_is_not():
    base = [("2026-11-0%d" % d, "08:00", "09:00") for d in range(3, 8)]
    old = hall(base)
    assert compare_hall(old, hall(base[:-1] + [("2026-11-07", "10:00", "11:00")]), "2026-11-02") == []
    assert compare_hall(old, hall([(d, "12:00", "13:00") for d, _, _ in base]), "2026-11-02")


def test_week_rollover_is_not_flagged():
    old = hall([("2026-10-27", "08:00", "09:00")], cov=("2026-10-26", "2026-11-01"))
    new = hall([("2026-11-03", "08:00", "09:00")], cov=("2026-11-02", "2026-11-08"))
    assert compare_hall(old, new, "2026-11-02") == []


def test_zero_sessions_is_not_flagged_when_the_source_says_it_is_empty(tmp_path, monkeypatch):
    import json

    from pipeline import anomalies

    halls = tmp_path / "halls"
    halls.mkdir()
    (halls / "x.json").write_text(json.dumps({**hall([]), "adapter": "a"}))
    sources = tmp_path / "sources.json"
    monkeypatch.setattr(anomalies, "HALLS_DIR", halls)
    monkeypatch.setattr(anomalies, "SOURCES_FILE", sources)
    monkeypatch.setattr(anomalies, "ROOT", tmp_path)
    monkeypatch.setattr(anomalies, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({"halls": {"x": {}}}))

    def review(src):
        sources.write_text(json.dumps([{"adapter": "a", "status": "ok", **src}]))
        anomalies.main(["--today", "2026-11-02"])
        return json.loads((tmp_path / "anomalies.json").read_text())["review"]

    assert any("zero sessions" in r for r in review({}))
    assert not any("zero sessions" in r for r in review({"empty_reason": "closed for the season"}))


class FakeAdapter:
    NAME = "fake"
    MUNICIPALITY = "Fakeby"
    SOURCE_URL = "https://example.com/"
    fail = False

    def fetch(self):
        return {"page": "<html></html>"}

    def parse(self, raw, today):
        from adapters.common import AdapterResult, HallResult, Session
        if self.fail:
            raise ValueError("layout changed")
        s = Session("2026-11-03", "08:00", "09:00", "shared", self.SOURCE_URL)
        return AdapterResult("2026-11-02", "2026-11-08", None, {"fake-hall": HallResult("fake-hall", "Hall", None, [s])})


def _use_tmp_data(tmp_path, monkeypatch):
    from pipeline import run
    halls = tmp_path / "halls"
    halls.mkdir()
    monkeypatch.setattr(run, "HALLS_DIR", halls)
    monkeypatch.setattr(run, "SOURCES_FILE", tmp_path / "sources.json")
    return run, halls / "fake-hall.json"


def test_unchanged_schedule_leaves_the_hall_file_untouched(tmp_path, monkeypatch):
    from datetime import date
    run, path = _use_tmp_data(tmp_path, monkeypatch)
    a = FakeAdapter()
    src = run.run_adapter(a, date(2026, 11, 2), "2026-11-02T06:17:00+01:00")
    first = path.read_bytes()
    src2 = run.run_adapter(a, date(2026, 11, 2), "2026-11-02T12:17:00+01:00")
    assert path.read_bytes() == first
    assert b"fetched_at" not in first and b"last_attempt_at" not in first
    # The run times are kept on the source instead.
    assert src["fetched_at"] == "2026-11-02T06:17:00+01:00"
    assert src2["fetched_at"] == src2["last_attempt_at"] == "2026-11-02T12:17:00+01:00"


def test_failed_source_marks_halls_stale_and_keeps_the_last_good_fetch_time(tmp_path, monkeypatch):
    import json
    from datetime import date
    run, path = _use_tmp_data(tmp_path, monkeypatch)
    a = FakeAdapter()
    good = run.run_adapter(a, date(2026, 11, 2), "2026-11-02T06:17:00+01:00")
    run.SOURCES_FILE.write_text(json.dumps([good]))
    a.fail = True
    src = run.run_adapter(a, date(2026, 11, 2), "2026-11-02T12:17:00+01:00")
    hall = json.loads(path.read_text())
    assert hall["status"] == "stale" and len(hall["sessions"]) == 1
    assert src["status"] == "error"
    assert src["fetched_at"] == "2026-11-02T06:17:00+01:00"
    assert src["last_attempt_at"] == "2026-11-02T12:17:00+01:00"
    # A hall that never had good data stays "error".
    path.write_text(json.dumps({**hall, "status": "error"}))
    run.run_adapter(a, date(2026, 11, 2), "2026-11-02T16:17:00+01:00")
    assert json.loads(path.read_text())["status"] == "error"
