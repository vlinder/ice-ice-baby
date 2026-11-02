from datetime import date

import pytest

from adapters import frigo, sodertalje_frigo
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 2)


# A list for several facilities, as Ekerö published until it moved to Interbook (Nov 2026).
PLACE_URL = ("https://ekero.fri-go.se/bokning/lista.aspx?dagar=28&typer=7"
             "&falt=tid,fullnamn,namn,eget,eget2,delt&schema=Ishallar&c=6937")
PLACE_HALLS = [
    frigo.Hall("ekero-trakvistavallen", "Träkvistavallen", "Vikingahallen", place="Träkvistavallen"),
    frigo.Hall("ekero-svanangen-ip", "Svanängen IP", "Allhallen", place="Svanängen IP"),
]


def parse_place(html):
    return frigo.parse_lists({PLACE_URL: html}, TODAY, halls=PLACE_HALLS, muni="ekero")


def ekero_raw():
    return (FIXTURES / "frigo_place_list.html").read_text(encoding="utf-8")


def test_place_list():
    r = parse_place(ekero_raw())
    assert r.warnings == [] and r.empty_reason is None
    assert (r.covers_from, r.covers_to) == ("2026-11-02", "2026-11-29")
    assert {k: len(v.sessions) for k, v in r.halls.items()} == {"ekero-trakvistavallen": 29, "ekero-svanangen-ip": 20}
    t = [(s.date, s.start, s.end, s.stick) for s in r.halls["ekero-trakvistavallen"].sessions]
    assert t[0] == ("2026-11-02", "14:30", "16:20", "with_stick")
    assert ("2026-11-06", "18:00", "19:40", "with_stick") in t
    assert ("2026-11-14", "13:40", "15:00", "no_stick") in t
    assert r.halls["ekero-svanangen-ip"].sessions[0].stick == "no_stick"


def test_sodertalje_empty_lists_are_an_explicit_empty():
    empty = (FIXTURES / "frigo_sodertalje_empty.html").read_text(encoding="utf-8")
    r = sodertalje_frigo.parse({url: empty for _, url in sodertalje_frigo.LISTS.values()}, TODAY)
    assert r.empty_reason
    assert set(r.halls) == set(sodertalje_frigo.LISTS)
    assert all(h.sessions == [] for h in r.halls.values())
    assert (r.covers_from, r.covers_to) == ("2026-11-02", "2026-11-08")


def test_sodertalje_rows_go_to_the_list_hall():
    url = sodertalje_frigo.LISTS["sodertalje-manskensrinken"][1]
    html = """<table id="tblLista"><tr><th colspan="4"><span>Lördag&nbsp;2026-11-07</span></th></tr>
    <tr><td>13:00-15:00</td><td>Allmänhetens åkning</td><td>Södertälje kommun</td><td>Utan klubba</td></tr></table>"""
    r = sodertalje_frigo.parse({url: html}, TODAY)
    s = r.halls["sodertalje-manskensrinken"].sessions[0]
    assert (s.date, s.start, s.end, s.stick) == ("2026-11-07", "13:00", "15:00", "no_stick")
    assert r.empty_reason is None


def test_permission_error_page_fails_loudly():
    with pytest.raises(ParseError):
        parse_place("<p>Error: Behörighet saknas för att använda denna sida</p>")


def test_no_schedules_found_means_no_times():
    # Ekerö's list between seasons (live answer on 2026-11-02).
    html = '<table id="tblLista"><tr><td><span>Error: Inga scheman funna</span></td></tr></table>'
    r = parse_place(html)
    assert r.empty_reason and "Inga scheman funna" in r.empty_reason
    assert set(r.halls) == {"ekero-trakvistavallen", "ekero-svanangen-ip"}
    assert all(h.sessions == [] for h in r.halls.values())
    assert (r.covers_from, r.covers_to) == ("2026-11-02", "2026-11-29")


def test_other_error_answers_are_a_source_failure():
    html = '<table id="tblLista"><tr><td><span>Error: Behörighet saknas</span></td></tr></table>'
    with pytest.raises(ParseError):
        parse_place(html)
