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


def test_high_share_of_few_changed_sessions_is_not_flagged():
    # 1 added, 2 removed out of 4: 75 % changed, but only 3 sessions.
    old = hall([("2026-11-03", "08:00", "09:00"), ("2026-11-04", "08:00", "09:00"), ("2026-11-05", "08:00", "09:00")])
    new = hall([("2026-11-03", "08:00", "09:00"), ("2026-11-06", "08:00", "09:00")])
    assert compare_hall(old, new, "2026-11-02") == []


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


class RenamingAdapter(FakeAdapter):
    """A source that calls the same rink "Halvmånar" one week and "Halvcirklar" the next."""
    NAME = "renaming"

    def parse(self, raw, today):
        from adapters.common import AdapterResult, HallResult, Session
        s1 = Session("2026-11-03", "08:00", "09:00", "no_stick", self.SOURCE_URL)
        s2 = Session("2026-11-10", "08:00", "09:00", "no_stick", self.SOURCE_URL)
        return AdapterResult("2026-11-02", "2026-11-15", None, {
            "x-halvmanar": HallResult("x-halvmanar", "X", "Halvmånar", [s1]),
            "x-halvcirklar": HallResult("x-halvcirklar", "X", "Halvcirklar", [s2]),
        }, warnings=["x-halvcirklar: could not read 'Onsdag: ?'"])


def test_aliases_file_sessions_and_problems_under_one_hall(tmp_path, monkeypatch):
    import json
    from datetime import date
    run, _ = _use_tmp_data(tmp_path, monkeypatch)
    halls = run.HALLS_DIR
    # A file left under an id that has since become an alias is removed.
    (halls / "x-halvcirklar.json").write_text(json.dumps({"hall_id": "x-halvcirklar", "adapter": "renaming"}))
    aliases = {"x-halvmanar": "x-uteisen-halvmanar", "x-halvcirklar": "x-uteisen-halvmanar"}
    run.run_adapter(RenamingAdapter(), date(2026, 11, 2), "2026-11-02T06:17:00+01:00", aliases=aliases)
    assert sorted(p.name for p in halls.iterdir()) == ["x-uteisen-halvmanar.json"]
    d = json.loads((halls / "x-uteisen-halvmanar.json").read_text())
    assert [s["date"] for s in d["sessions"]] == ["2026-11-03", "2026-11-10"]
    assert d["covers"] == {"from": "2026-11-02", "to": "2026-11-15"}
    assert [p["hall_id"] for p in d["problems"]] == ["x-uteisen-halvmanar"]


def test_aliases_in_config_must_be_unambiguous(tmp_path):
    import json

    import pytest

    from pipeline.run import load_aliases
    cfg = tmp_path / "halls.json"
    cfg.write_text(json.dumps({"halls": {"a": {"aliases": ["b"]}, "b": {}}}))
    with pytest.raises(ValueError):
        load_aliases(cfg)
    cfg.write_text(json.dumps({"halls": {"a": {"aliases": ["c"]}, "b": {"aliases": ["c"]}}}))
    with pytest.raises(ValueError):
        load_aliases(cfg)
    cfg.write_text(json.dumps({"halls": {"a": {"aliases": ["c", "d"]}, "b": {}}}))
    assert load_aliases(cfg) == {"c": "a", "d": "a"}


def test_real_config_aliases_and_parts_load():
    from pipeline.run import load_aliases, load_parts
    load_aliases()
    parts = load_parts()
    hall, name, _ = parts["stockholm-ostermalms-idrottsplats-rundbana-och-innerplan"]
    assert (hall, name) == ("stockholm-ostermalms-idrottsplats-uteisen", "rundbana och innerplan")
    assert parts["stockholm-ostermalms-idrottsplats-uteisen-halvmanar"][0] == hall


def _s(day, start, end, stick, note=None):
    from adapters.common import Session
    return Session(day, start, end, stick, "https://example.com/", note=note)


def test_shared_ice_is_cut_where_the_rules_change():
    from pipeline.run import share_sessions
    d = "2026-11-27"
    rundbana = [_s(d, "08:30", "16:00", "no_stick"), _s(d, "19:00", "23:00", "no_stick")]
    halvmanar = [_s(d, "17:00", "23:00", "with_stick")]
    out = share_sessions([(0, "rundbana och innerplan", rundbana), (1, "halvmånarna", halvmanar)])
    assert [(x.start, x.end, x.stick, x.note) for x in out] == [
        ("08:30", "16:00", "no_stick", "Rundbana och innerplan"),
        ("17:00", "19:00", "with_stick", "Halvmånarna"),
        ("19:00", "23:00", "shared", "Rundbana och innerplan utan klubba, halvmånarna med klubba"),
    ]


def test_shared_ice_whole_ice_same_rule_and_adjacent_sessions_join():
    from pipeline.run import share_sessions
    d = "2026-12-01"
    rundbana = [_s(d, "19:00", "20:00", "no_stick")]
    halvmanar = [_s(d, "17:00", "18:00", "no_stick"), _s(d, "18:00", "19:00", "no_stick"),
                 _s(d, "19:00", "23:00", "no_stick")]
    out = share_sessions([(0, "rundbana och innerplan", rundbana), (1, "halvmånarna", halvmanar)])
    assert [(x.start, x.end, x.stick, x.note) for x in out] == [
        ("17:00", "19:00", "no_stick", "Halvmånarna"),  # back-to-back with the same rules: one session
        ("19:00", "20:00", "no_stick", "Hela isen"),
        ("20:00", "23:00", "no_stick", "Halvmånarna"),
    ]


def test_shared_ice_unknown_rule_is_not_called_shared():
    from pipeline.run import share_sessions
    d = "2026-11-28"
    out = share_sessions([(0, "a", [_s(d, "10:00", "12:00", "no_stick")]),
                          (1, "b", [_s(d, "10:00", "12:00", "unknown", "familjeåkning")])])
    assert [(x.stick, x.note) for x in out] == [("unknown", "A utan klubba, b (familjeåkning)")]


class PartsAdapter(FakeAdapter):
    NAME = "parts"

    def parse(self, raw, today):
        from adapters.common import AdapterResult, HallResult
        d = "2026-11-03"
        return AdapterResult("2026-11-02", "2026-11-08", None, {
            "x-oval": HallResult("x-oval", "X", "Oval", [_s(d, "08:00", "12:00", "no_stick")]),
            "x-ends": HallResult("x-ends", "X", "Ends", [_s(d, "10:00", "12:00", "with_stick")]),
            "x-hall": HallResult("x-hall", "X", "Hall", [_s(d, "09:00", "10:00", "shared")]),
        }, warnings=["x-ends: could not read 'Onsdag: ?'"])


def test_parts_fold_into_one_hall_and_old_files_go(tmp_path, monkeypatch):
    import json
    from datetime import date
    run, _ = _use_tmp_data(tmp_path, monkeypatch)
    halls = run.HALLS_DIR
    (halls / "x-oval.json").write_text(json.dumps({"hall_id": "x-oval", "adapter": "parts"}))
    parts = {"x-oval": ("x-ice", "the oval", 0), "x-ends": ("x-ice", "the ends", 1)}
    run.run_adapter(PartsAdapter(), date(2026, 11, 2), "2026-11-02T06:17:00+01:00", parts=parts)
    assert sorted(p.name for p in halls.iterdir()) == ["x-hall.json", "x-ice.json"]
    d = json.loads((halls / "x-ice.json").read_text())
    assert [(s["start"], s["end"], s["stick"], s["note"]) for s in d["sessions"]] == [
        ("08:00", "10:00", "no_stick", "The oval"),
        ("10:00", "12:00", "shared", "The oval utan klubba, the ends med klubba")]
    assert [p["hall_id"] for p in d["problems"]] == ["x-ice"]


def test_parts_in_config_must_be_unambiguous(tmp_path):
    import json

    import pytest

    from pipeline.run import load_parts
    cfg = tmp_path / "halls.json"
    cfg.write_text(json.dumps({"halls": {"a": {"parts": [{"name": "p", "ids": ["b"]}]}, "b": {}}}))
    with pytest.raises(ValueError):
        load_parts(cfg)
    cfg.write_text(json.dumps({"halls": {"a": {"parts": [{"name": "p", "ids": ["c"]}, {"name": "q", "ids": ["c"]}]}}}))
    with pytest.raises(ValueError):
        load_parts(cfg)


def test_index_changed_at_follows_the_hall_file_contents(tmp_path, monkeypatch):
    import json

    from pipeline import build_index, validate
    halls = tmp_path / "halls"
    halls.mkdir()
    for mod in (build_index, validate):
        monkeypatch.setattr(mod, "HALLS_DIR", halls)
        monkeypatch.setattr(mod, "INDEX_FILE", tmp_path / "index.json")
    monkeypatch.setattr(build_index, "SOURCES_FILE", tmp_path / "sources.json")
    monkeypatch.setattr(build_index, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(validate, "DATA", tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"halls": {}}))
    (tmp_path / "sources.json").write_text("[]")
    for hid in ("a", "b"):
        (halls / f"{hid}.json").write_text(json.dumps({**hall([("2026-11-03", "08:00", "09:00")]),
                                                      "hall_id": hid, "adapter": "x"}))
    clock = iter(["2026-11-02T06:00:00+01:00", "2026-11-02T12:00:00+01:00", "2026-11-02T17:00:00+01:00"])
    monkeypatch.setattr(build_index, "now_local", lambda: type("T", (), {"isoformat": lambda self, t=next(clock): t})())

    def build():
        build_index.main()
        return {h["id"]: (h["version"], h["changed_at"]) for h in json.loads((tmp_path / "index.json").read_text())["halls"]}

    first = build()
    assert first["a"][1] == first["b"][1] == "2026-11-02T06:00:00+01:00"
    assert first["a"][0] == build_index.file_version(halls / "a.json")
    second = build()  # nothing changed: versions and times carry over
    assert second == first
    (halls / "b.json").write_text((halls / "b.json").read_text().replace("09:00", "09:30"))
    third = build()
    assert third["a"] == first["a"]
    assert third["b"][0] != first["b"][0] and third["b"][1] == "2026-11-02T17:00:00+01:00"


def test_validate_rejects_a_stale_version(tmp_path, monkeypatch, capsys):
    import json

    from pipeline import build_index, validate
    halls = tmp_path / "halls"
    halls.mkdir()
    for mod in (build_index, validate):
        monkeypatch.setattr(mod, "HALLS_DIR", halls)
        monkeypatch.setattr(mod, "INDEX_FILE", tmp_path / "index.json")
    monkeypatch.setattr(build_index, "SOURCES_FILE", tmp_path / "sources.json")
    monkeypatch.setattr(build_index, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(validate, "DATA", tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"halls": {}}))
    (tmp_path / "sources.json").write_text("[]")
    (halls / "a.json").write_text(json.dumps({**hall([("2026-11-03", "08:00", "09:00")]), "spec_version": 2,
                                              "hall_id": "a", "adapter": "x", "error": None}))
    build_index.main()
    validate.main()
    assert "does not match the file" not in capsys.readouterr().err
    (halls / "a.json").write_text((halls / "a.json").read_text().replace("09:00", "09:30"))
    assert validate.main() == 1  # the file changed but index.json was not rebuilt
    assert "version" in capsys.readouterr().err


def test_a_part_under_two_wordings_still_counts_once():
    from adapters.common import AdapterResult, HallResult
    from pipeline.run import merge_parts
    d1, d2 = "2026-11-03", "2026-11-10"  # the source renamed the parts between the weeks
    r = AdapterResult("2026-11-02", "2026-11-15", None, {
        "oval-w1": HallResult("oval-w1", "X", "Oval", [_s(d1, "19:00", "20:00", "no_stick")]),
        "ends-w1": HallResult("ends-w1", "X", "Ends", [_s(d1, "19:00", "20:00", "no_stick")]),
        "oval-w2": HallResult("oval-w2", "X", "Rundbana", [_s(d2, "19:00", "20:00", "no_stick")]),
        "ends-w2": HallResult("ends-w2", "X", "Halvmånar", [_s(d2, "19:00", "20:00", "no_stick")]),
    })
    parts = {"oval-w1": ("ice", "oval", 0), "oval-w2": ("ice", "oval", 0),
             "ends-w1": ("ice", "ends", 1), "ends-w2": ("ice", "ends", 1)}
    out = merge_parts(r, parts).halls["ice"].sessions
    assert [(x.date, x.note) for x in out] == [(d1, "Hela isen"), (d2, "Hela isen")]
