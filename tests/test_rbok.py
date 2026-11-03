"""Rbok boards: the shared parser and each municipality's hall mapping, against
board pages saved on 2026-11-02."""
from datetime import date

from adapters import botkyrka_rbok, haninge_rbok, nacka_rbok, norrtalje_rbok, rbok, vallentuna_rbok
from conftest import FIXTURES

TODAY = date(2026, 11, 2)


def load(module):
    name = module.__name__.split(".")[-1].removesuffix("_rbok")
    return {b: (FIXTURES / f"rbok_{name}_{b[:8]}.html").read_text(encoding="utf-8") for b in module.BOARDS}


def run(module):
    r = module.parse(load(module), TODAY)
    assert r.warnings == []
    return r


def sessions(r, hid):
    return [(s.date, s.start, s.end, s.stick) for s in r.halls[hid].sessions]


def table(rows, by=False):
    head = "<td>Datum</td><td>Tid</td><td>Resurs</td>" + ("<td>Bokad av</td>" if by else "") + "<td>Information</td>"
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def test_botkyrka():
    r = run(botkyrka_rbok)
    assert set(r.halls) == {"botkyrka-brantbrinks-ip", "botkyrka-tumba-ishuset-hall-2"}
    assert ("2026-11-02", "14:00", "15:45", "with_stick") in sessions(r, "botkyrka-brantbrinks-ip")
    assert ("2026-11-04", "14:15", "16:00", "no_stick") in sessions(r, "botkyrka-tumba-ishuset-hall-2")
    # The board is full (100 rows) and ends on 19 Dec, which may be cut short.
    assert r.covers_to == "2026-12-18"
    assert all(s.date <= "2026-12-18" for h in r.halls.values() for s in h.sessions)


def test_haninge_boards_have_their_own_horizon():
    r = run(haninge_rbok)
    assert set(r.halls) == {"haninge-hanvedens-ip", "haninge-torvalla-ip-ishall-1", "haninge-torvalla-ip-ishall-2"}
    assert r.halls["haninge-torvalla-ip-ishall-2"].covers_to == "2026-12-01"
    assert r.halls["haninge-torvalla-ip-ishall-1"].covers_to == "2026-12-18"
    assert r.halls["haninge-hanvedens-ip"].covers_to == "2026-12-21"
    assert ("2026-11-02", "14:00", "15:30", "no_stick") in sessions(r, "haninge-torvalla-ip-ishall-1")
    assert r.halls["haninge-torvalla-ip-ishall-1"].sessions[0].source_url.endswith(haninge_rbok.BOARDS[1])


def test_haninge_half_rinks_become_shared():
    r = run(haninge_rbok)
    monday = [s for s in sessions(r, "haninge-hanvedens-ip") if s[0] == "2026-11-02"]
    assert monday == [("2026-11-02", "14:00", "15:45", "shared")]
    assert ("2026-11-06", "14:00", "20:00", "with_stick") in sessions(r, "haninge-hanvedens-ip")


def test_nacka():
    r = run(nacka_rbok)
    assert set(r.halls) == {
        "nacka-nacka-ip-ishall", "nacka-nacka-ip-istalt", "nacka-bjorknas-ip-ishall",
        "nacka-bjorknas-ip-istalt", "nacka-alta-ishall", "nacka-saltsjobadens-ishall"}
    assert ("2026-11-03", "18:00", "19:00", "no_stick") in sessions(r, "nacka-alta-ishall")
    assert ("2026-11-07", "11:45", "13:45", "with_stick") in sessions(r, "nacka-nacka-ip-istalt")
    assert all(s.note is None for h in r.halls.values() for s in h.sessions)
    assert (r.covers_from, r.covers_to) == ("2026-11-02", "2026-11-22")


def test_norrtalje():
    r = run(norrtalje_rbok)
    assert set(r.halls) == {"norrtalje-sportcentrum-ishall"}
    assert sessions(r, "norrtalje-sportcentrum-ishall")[:2] == [
        ("2026-11-02", "14:00", "16:00", "no_stick"), ("2026-11-03", "14:00", "16:00", "with_stick")]


def test_vallentuna():
    r = run(vallentuna_rbok)
    assert set(r.halls) == {"vallentuna-vallentuna-ip-ishall-a", "vallentuna-vallentuna-ip-ishall-b"}
    assert ("2026-11-03", "13:00", "15:00", "with_stick") in sessions(r, "vallentuna-vallentuna-ip-ishall-b")
    assert r.covers_to == "2026-12-01"


def test_unknown_resource_and_unknown_wording():
    html = table([("Lör 07 Nov", "10:00 - 11:00", "Nya IP / Rink 3", "Familjeåkning")])
    r = rbok.parse_boards({"b": html}, TODAY, host="x.rbok.se", halls=[], muni="x")
    s = r.halls["x-nya-ip-rink-3"].sessions[0]
    assert (s.stick, s.note) == ("unknown", "Familjeåkning")


def test_rows_not_booked_as_public_skating_are_warned_about():
    html = table([("Lör 07 Nov", "10:00 - 11:00", "Torvalla IP / Ishall 1", "Hockeyklubben", "Med klubba")], by=True)
    r = haninge_rbok.parse({"b": html}, TODAY)
    assert r.halls == {} and len(r.warnings) == 1


def test_repeated_rows_are_deduped():
    row = ("Lör 07 Nov", "10:00 - 11:00", "Vallentuna IP / Ishall A", "Utan klubba")
    r = vallentuna_rbok.parse({"b": table([row, row])}, TODAY)
    assert len(r.halls["vallentuna-vallentuna-ip-ishall-a"].sessions) == 1
