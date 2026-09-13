import codecs
from datetime import datetime

import pytest

from raffle import samples
from raffle.loader import decode_text, load_file, load_people_list
from raffle.models import KIND_EVENT_LOG, KIND_LIST, KIND_REPORT


def utf16(text: str) -> bytes:
    return codecs.BOM_UTF16_LE + text.replace("\n", "\r\n").encode("utf-16-le")


def only_meeting(data: bytes, name: str = "file.csv"):
    result = load_file(data, name)
    assert not result.error, result.error
    assert len(result.meetings) == 1
    return result.meetings[0]


def by_name(meeting):
    return {a.name: a for a in meeting.attendees}


def test_teams_report_english_sample():
    meeting = only_meeting(samples.teams_report_en())
    assert meeting.kind == KIND_REPORT
    assert meeting.title == "Cloud Cost Basics"
    assert meeting.start and meeting.end and meeting.start < meeting.end
    assert all(a.email or "(Guest)" in a.name for a in meeting.attendees)
    assert {a.role for a in meeting.attendees} == {"Organizer", "Attendee"}
    reconnected = [a for a in meeting.attendees if len(a.sessions) > 1]
    assert reconnected, "the detailed activities section must be used for timing"


def test_teams_report_portuguese_sample_roles_are_translated():
    meeting = only_meeting(samples.teams_report_pt())
    assert meeting.kind == KIND_REPORT
    assert meeting.title == "Introdução a Dados"
    assert {a.role for a in meeting.attendees} == {"Organizer", "Attendee"}
    assert meeting.day.isoformat() == "2026-03-17"


def test_classic_attendance_list_sample():
    meeting = only_meeting(samples.teams_attendance_list_classic())
    assert meeting.kind == KIND_EVENT_LOG
    assert meeting.end is None
    assert any(left is None for a in meeting.attendees for _, left in a.sessions)
    assert any("does not say when the meeting ended" in w for w in meeting.warnings)


def test_plain_text_list_keeps_commas_inside_names():
    meeting = only_meeting(samples.checkin_list_txt(), "list.txt")
    assert meeting.kind == KIND_LIST
    names = [a.name for a in meeting.attendees]
    assert len(names) == 25
    assert any("," in name for name in names)
    assert not any(name[0].isdigit() for name in names)


def test_spreadsheet_with_first_and_last_name_columns():
    meeting = only_meeting(samples.registrations_xlsx(), "registrations.xlsx")
    assert meeting.kind == KIND_LIST
    assert "Avery Nimbus" in by_name(meeting)
    assert by_name(meeting)["Avery Nimbus"].email == "avery.nimbus@example.com"


def test_classic_list_without_header_and_quotes():
    data = utf16(
        '"Doe, Jane"\tJoined before\t"13/09/2022, 5:30:21 PM"\n'
        'Sam Roe\tJoined\t"13/09/2022, 5:31:00 PM"\n'
        'Sam Roe\tLeft\t"13/09/2022, 5:45:00 PM"\n'
    )
    meeting = only_meeting(data)
    assert meeting.kind == KIND_EVENT_LOG
    people = by_name(meeting)
    assert people["Doe, Jane"].sessions == [(datetime(2022, 9, 13, 17, 30, 21), None)]
    assert people["Sam Roe"].sessions == [(datetime(2022, 9, 13, 17, 31), datetime(2022, 9, 13, 17, 45))]


def test_portuguese_classic_list_header_and_actions():
    data = utf16("Nome Completo\tAtividade\tCarimbo de data/hora\n"
                 "Ana Lima\tIngressou\t05/09/2022 16:29:58\n"
                 "Ana Lima\tSaiu\t05/09/2022 17:29:58\n")
    meeting = only_meeting(data)
    assert meeting.kind == KIND_EVENT_LOG
    joined, left = meeting.attendees[0].sessions[0]
    assert (left - joined).total_seconds() == 3600


@pytest.mark.parametrize("header, organizer", [
    ("Nombre\tHora de unión\tHora de salida\tDuración\tCorreo electrónico\tRol", "Organizador"),
    ("Nom\tHeure d'arrivée\tHeure de départ\tDurée\tE-mail\tRôle", "Organisateur"),
    ("Name\tBeitrittszeit\tVerlassenszeit\tDauer\tE-Mail\tRolle", "Organisator"),
    ("Nome\tOra di ingresso\tOra di uscita\tDurata\tEmail\tRuolo", "Organizzatore"),
    ("Naam\tTijd van deelname\tTijd van vertrek\tDuur\tE-mail\tRol", "Organisator"),
])
def test_report_headers_in_other_languages(header, organizer):
    data = utf16(f"{header}\nKim Lee\t2026-03-03 17:00:00\t2026-03-03 18:00:00\t1h\tkim@example.com\t{organizer}\n")
    meeting = only_meeting(data)
    assert meeting.kind == KIND_REPORT
    kim = by_name(meeting)["Kim Lee"]
    assert (kim.email, kim.role, len(kim.sessions)) == ("kim@example.com", "Organizer", 1)


@pytest.mark.parametrize("joined, left", [
    ("Se unió", "Salió"), ("A rejoint", "A quitté"), ("Beigetreten", "Verlassen"), ("Entrou antes de", "Saiu"),
])
def test_classic_list_actions_in_other_languages(joined, left):
    data = utf16(f"Kim Lee\t{joined}\t2026-03-03 17:00:00\nKim Lee\t{left}\t2026-03-03 17:30:00\n")
    assert by_name(only_meeting(data))["Kim Lee"].sessions == [(datetime(2026, 3, 3, 17), datetime(2026, 3, 3, 17, 30))]


def test_utf16_without_bom_is_detected():
    data = "Full Name\tUser Action\tTimestamp\nJo Park\tJoined\t2026-01-02 10:00:00\n".encode("utf-16-le")
    assert by_name(only_meeting(data))["Jo Park"].sessions


def test_excel_style_semicolon_csv_in_cp1252():
    data = "Nome;E-mail;Função\nJoão Araújo;joao@example.com;Participante\n".encode("cp1252")
    meeting = only_meeting(data)
    assert meeting.kind == KIND_LIST
    assert by_name(meeting)["João Araújo"].email == "joao@example.com"


def test_zoom_like_participants_export():
    data = ("Meeting ID,Topic,Start Time,End Time,User Email,Duration (Minutes),Participants\n"
            "123,Weekly,01/10/2026 10:00:00 AM,01/10/2026 11:00:00 AM,host@example.com,60,2\n"
            "\n"
            "Name (Original Name),User Email,Join Time,Leave Time,Duration (Minutes),Guest\n"
            "Kim Lee,kim@example.com,01/10/2026 10:01:00 AM,01/10/2026 10:59:00 AM,58,No\n"
            "Guest Person,,01/10/2026 10:05:00 AM,01/10/2026 10:20:00 AM,15,Yes\n").encode("utf-8")
    meeting = only_meeting(data)
    assert meeting.kind == KIND_REPORT
    people = by_name(meeting)
    assert set(people) == {"Kim Lee", "Guest Person"}
    assert people["Kim Lee"].email == "kim@example.com"


def test_google_meet_like_export():
    data = ("First name,Last name,Email,Duration,Time joined,Time exited\n"
            "Ada,Nova,ada@example.com,1 hr 2 min,10:00 AM,11:02 AM\n").encode("utf-8")
    meeting = only_meeting(data)
    person = by_name(meeting)["Ada Nova"]
    assert person.email == "ada@example.com"


def test_file_with_two_meetings_is_split():
    data = utf16("Full Name\tUser Action\tTimestamp\n"
                 "Ana\tJoined\t11/10/2022 17:30\n"
                 "Bo\tJoined\t11/10/2022 17:31\n"
                 "Cy\tJoined before\t27/09/2022 17:27\n")
    result = load_file(data, "mixed.csv")
    assert len(result.meetings) == 2
    assert sorted(len(m.attendees) for m in result.meetings) == [1, 2]
    assert all("split" in " ".join(m.warnings) for m in result.meetings)


def test_ambiguous_dates_produce_a_warning():
    data = utf16("Full Name\tUser Action\tTimestamp\nAna\tJoined\t05/09/2022 16:29\n")
    assert any("ambiguous" in w for w in only_meeting(data).warnings)


def test_duplicate_rows_for_the_same_person_are_merged():
    data = "Name,Email\nJane Doe,jane@example.com\nJANE DOE,Jane@Example.com\nDoe, Jane\n".encode("utf-8")
    meeting = only_meeting(data)
    assert len([a for a in meeting.attendees if a.email]) == 1


@pytest.mark.parametrize("data, name, message", [
    (b"", "empty.csv", "empty"),
    (b"\x00\x01\x02\x03binary\x00\xff" * 20, "photo.csv", "does not look like"),
    (b"whatever", "old.xls", ".xls"),
    (b"PK\x03\x04broken zip", "broken.xlsx", "Could not read"),
    ("1\n2\n3\n".encode(), "numbers.csv", "No participant names"),
])
def test_unreadable_files_report_friendly_errors(data, name, message):
    result = load_file(data, name)
    assert not result.meetings
    assert message in result.error


def test_decode_text_fallbacks():
    assert decode_text("Ação".encode("cp1252")) == "Ação"
    assert decode_text(codecs.BOM_UTF8 + "Zoë".encode("utf-8")) == "Zoë"


def test_people_list_prefers_emails():
    data = "Winner,Email\nJane Doe,jane@example.com\nNo Email Person,\n".encode("utf-8")
    assert load_people_list(data, "winners.csv") == ["jane@example.com", "No Email Person"]
