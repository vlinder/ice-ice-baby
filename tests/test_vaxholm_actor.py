from datetime import date

import pytest

from adapters import vaxholm_actor as a
from adapters.common import ParseError
from conftest import FIXTURES

TODAY = date(2026, 11, 2)
HID = "vaxholm-saneco-hallen"


def test_fixture_no_records_is_explicit_empty():
    raw = {"page": (FIXTURES / "vaxholm_actor.html").read_text(encoding="utf-8")}
    r = a.parse(raw, TODAY)
    assert list(r.halls) == [HID]
    assert r.halls[HID].sessions == []
    assert r.empty_reason
    assert (r.covers_from, r.covers_to) == ("2026-11-02", "2026-11-29")
    assert r.warnings == []


def _grid(body: str) -> dict:
    """The fixture's grid markup with the tbody replaced."""
    html = (FIXTURES / "vaxholm_actor.html").read_text(encoding="utf-8")
    start = html.index('<tr class="rgNoRecords">')
    end = html.index("</tr>", start) + len("</tr>")
    return {"page": html[:start] + body + html[end:]}


def _row(lokal, start, slut, info, cls="rgRow"):
    return f'<tr class="{cls}" id="RadGrid1_ctl00__0"><td>{lokal}</td><td>{start}</td><td>{slut}</td><td>{info}</td></tr>'


def test_rows_parse():
    r = a.parse(_grid(
        _row("Saneco Hallen", "2026-11-05 18:00", "2026-11-05 19:30", "Allmänhetens åkning utan klubba")
        + _row("Saneco Hallen", "2026-11-08 13:00:00", "2026-11-08 14:50:00", "Allmänhetens åkning", "rgAltRow")
        + _row("Saneco Hallen", "2026-11-08 13:00:00", "2026-11-08 14:50:00", "Allmänhetens åkning")
        + _row("Saneco Hallen", "2026-11-09 21:00", "2026-11-10 00:00", "Halva isen med klubba")
        + _row("Saneco Hallen", "2026-11-12 10:00", "2026-11-12 11:00", "Inställd")
        + _row("Saneco Hallen", "2026-11-01 10:00", "2026-11-01 11:00", "")
    ), TODAY)
    s = r.halls[HID].sessions
    assert [(x.date, x.start, x.end, x.stick, x.note) for x in s] == [
        ("2026-11-05", "18:00", "19:30", "no_stick", None),
        ("2026-11-08", "13:00", "14:50", "unknown", "Allmänhetens åkning"),
        ("2026-11-09", "21:00", "24:00", "shared", None),
    ]
    assert (r.covers_from, r.covers_to) == ("2026-11-02", "2026-11-12")
    assert r.empty_reason is None


def test_other_lokal_gets_own_hall():
    r = a.parse(_grid(_row("Vaxholms IP Konstfrys", "2026-11-05 18:00", "2026-11-05 19:30", "")), TODAY)
    assert set(r.halls) == {HID, "vaxholm-vaxholms-ip-konstfrys"}
    assert len(r.halls["vaxholm-vaxholms-ip-konstfrys"].sessions) == 1


@pytest.mark.parametrize("start,slut", [
    ("5/11 18:00", "5/11 19:30"),
    ("2026-11-05 18.00", "2026-11-05 19.30"),
    ("2026-11-05 18:00:30", "2026-11-05 19:30"),
    ("2026-11-05", "2026-11-05"),
    ("2026-11-05 18:00", "2026-11-07 19:30"),   # several days
    ("2026-11-05 19:30", "2026-11-05 18:00"),   # end before start
])
def test_unexpected_datetime_fails(start, slut):
    with pytest.raises(ParseError):
        a.parse(_grid(_row("Saneco Hallen", start, slut, "")), TODAY)


def test_layout_change_fails_loudly():
    with pytest.raises(ParseError):
        a.parse({"page": "<html><body><p>Ny design!</p></body></html>"}, TODAY)
    with pytest.raises(ParseError):  # unknown row type
        a.parse(_grid('<tr class="rgGroupHeader"><td colspan="4">Vecka 45</td></tr>'), TODAY)
    with pytest.raises(ParseError):  # other empty text
        a.parse(_grid('<tr class="rgNoRecords"><td colspan="4"><div>Inga poster.</div></td></tr>'), TODAY)
    html = (FIXTURES / "vaxholm_actor.html").read_text(encoding="utf-8").replace(">Slut<", ">Till<")
    with pytest.raises(ParseError):
        a.parse({"page": html}, TODAY)
