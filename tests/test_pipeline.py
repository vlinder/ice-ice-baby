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
