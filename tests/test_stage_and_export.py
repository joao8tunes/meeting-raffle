import io
import json
import re

import pandas as pd
from openpyxl import load_workbook

from raffle.draw import Entry, run_round
from raffle.export import participants_frame, results_workbook, to_csv, winners_frame
from raffle.models import KIND_LIST, Attendance, Meeting
from raffle.people import build_roster
from raffle.stage import RACE, REVEAL, SHUFFLE, WHEEL, build_idle_stage, build_stage, effective_mode

EVIL = '</script><img src=x onerror="alert(1)">'


def payload(html: str) -> dict:
    match = re.search(r"const DATA = (.*?);\n", html)
    return json.loads(match.group(1))


def test_names_cannot_break_out_of_the_stage_script():
    pool = [Entry("k1", EVIL), Entry("k2", "=cmd|' /C calc'!A0")] + [Entry(f"k{i}", f"P{i}") for i in range(3, 90)]
    draw = run_round(1, "<b>Prize</b>", pool, 2)
    for mode in (WHEEL, RACE, SHUFFLE, REVEAL):
        html = build_stage(draw, mode)
        assert "</script><img" not in html
        assert html.count("</script>") == 1
        data = payload(html)
        on_stage = data["names"]
        assert all(on_stage[w["slot"]] == w["name"] for w in data["winners"])
        assert [w["name"] for w in data["winners"]] == [w.name for w in draw.winners]


def test_stage_limits_names_on_screen_but_keeps_winners():
    pool = [Entry(f"k{i:03d}", f"Person {i}") for i in range(300)]
    draw = run_round(1, "", pool, 3)
    wheel = payload(build_stage(draw, WHEEL))
    assert len(wheel["names"]) == 60
    race = payload(build_stage(draw, RACE))
    assert len(race["names"]) == 8
    assert "300 eligible" in race["caption"]


def test_emails_only_on_stage_when_requested():
    draw = run_round(1, "", [Entry("k", "Kim", "kim@example.com")], 1)
    assert "kim@example.com" not in build_stage(draw, REVEAL)
    assert "kim@example.com" in build_stage(draw, REVEAL, show_email=True)


def test_big_rounds_fall_back_to_instant_reveal():
    assert effective_mode(WHEEL, 6)[0] == REVEAL
    assert effective_mode(RACE, 3) == (RACE, "")
    assert "Ready when you are" in payload(build_idle_stage("Mug", 10))["idleTitle"]


def test_exports_neutralize_spreadsheet_formulas():
    pool = [Entry("k1", "=HYPERLINK(\"http://x\")", "a@example.com"), Entry("k2", "+55 11 99999", "")]
    draw = run_round(1, "@prize", pool, 2)
    csv_text = to_csv(winners_frame([draw])).decode("utf-8-sig")
    assert "'=HYPERLINK" in csv_text and "'+55" in csv_text and "'@prize" in csv_text

    meeting = Meeting("list.csv", KIND_LIST, [Attendance("=SUM(A1)"), Attendance("Normal Name")])
    roster = build_roster([meeting])
    workbook = load_workbook(io.BytesIO(results_workbook([draw], roster, [meeting])))
    assert set(workbook.sheetnames) == {"Winners", "Audit", "Pools", "Participants", "Files"}
    values = [cell.value for row in workbook["Participants"].iter_rows() for cell in row]
    assert "'=SUM(A1)" in values
    assert all(cell.data_type != "f" for sheet in workbook for row in sheet.iter_rows() for cell in row)


def test_participants_frame_can_hide_emails():
    meeting = Meeting("list.csv", KIND_LIST, [Attendance("Kim", "kim@example.com")])
    roster = build_roster([meeting])
    assert "Email" in participants_frame(roster, [meeting]).columns
    assert "Email" not in participants_frame(roster, [meeting], include_email=False).columns
    assert isinstance(winners_frame([], include_email=False), pd.DataFrame)
