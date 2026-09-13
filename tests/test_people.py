from datetime import datetime, time

from raffle import samples
from raffle.loader import load_file
from raffle.models import KIND_EVENT_LOG, KIND_LIST, KIND_REPORT, Attendance, Meeting
from raffle.people import Rules, build_roster

DAY = datetime(2026, 3, 3)


def at(hour: int, minute: int = 0) -> datetime:
    return DAY.replace(hour=hour, minute=minute)


def report(*attendees, end=None, start=None):
    return Meeting(source="r.csv", kind=KIND_REPORT, attendees=list(attendees), start=start or at(17),
                   end=end or at(18), first_seen=start or at(17), last_seen=end or at(18))


def person(roster, name):
    return next(p for p in roster.people if p.name == name)


def test_sample_series_merges_people_across_formats_and_languages():
    meetings = []
    for name, data in samples.demo_files():
        meetings += load_file(data, name).meetings
    roster = build_roster(meetings)
    assert len(meetings) == 4
    assert len(roster.people) == 39  # one fictional person never attends
    assert max(p.meetings for p in roster.people) == 4
    guest = next(p for p in roster.people if "Topaz" in p.name)
    assert not guest.email and guest.meetings >= 2


def test_nobody_is_excluded_by_default():
    meetings = load_file(samples.teams_report_en(), "a.csv").meetings
    roster = build_roster(meetings)
    assert roster.eligible == roster.people


def test_names_match_regardless_of_order_case_and_accents():
    a = Meeting("a", KIND_LIST, [Attendance("Araújo, João")])
    b = Meeting("b", KIND_LIST, [Attendance("JOAO ARAUJO")])
    roster = build_roster([a, b])
    assert len(roster.people) == 1
    assert roster.people[0].name == "João Araújo"
    assert roster.people[0].meetings == 2


def test_namesakes_with_different_emails_stay_apart():
    a = Meeting("a", KIND_LIST, [Attendance("Ana Silva", "ana.silva@example.com"),
                                 Attendance("Ana Silva", "ana.silva2@example.com")])
    roster = build_roster([a])
    assert len(roster.people) == 2
    assert len({p.key for p in roster.people}) == 2


def test_email_links_different_spellings():
    a = Meeting("a", KIND_LIST, [Attendance("Robert Stone", "rob@example.com")])
    b = Meeting("b", KIND_LIST, [Attendance("Bob Stone", "ROB@example.com")])
    roster = build_roster([a, b])
    assert len(roster.people) == 1
    assert roster.people[0].aliases


def test_near_identical_names_merge_only_when_enabled():
    a = Meeting("a", KIND_LIST, [Attendance("Alnio Heleno Corradi")])
    b = Meeting("b", KIND_LIST, [Attendance("Alnio Helenio Corradi")])
    assert len(build_roster([a, b]).people) == 2
    merged = build_roster([a, b], Rules(merge_similar_names=True))
    assert len(merged.people) == 1
    assert merged.merged_names


def test_similar_names_with_conflicting_emails_never_merge():
    a = Meeting("a", KIND_LIST, [Attendance("Maria Souza", "m1@example.com"),
                                 Attendance("Maria Sousa", "m2@example.com")])
    assert len(build_roster([a], Rules(merge_similar_names=True)).people) == 2


def test_minimum_minutes_adds_reconnections_and_ignores_overlaps():
    meeting = report(
        Attendance("Dropped", sessions=[(at(17), at(17, 20)), (at(17, 25), at(17, 40))]),   # 35 min
        Attendance("Two devices", sessions=[(at(17), at(17, 30)), (at(17, 10), at(17, 30))]),  # 30 min, not 50
    )
    roster = build_roster([meeting], Rules(min_minutes=32))
    assert person(roster, "Dropped").eligible
    two = person(roster, "Two devices")
    assert round(two.minutes) == 30 and not two.eligible
    assert two.reason == "30 of 32 min"


def test_present_at_end_uses_grace_period():
    meeting = report(
        Attendance("Stayed", sessions=[(at(17), at(18))]),
        Attendance("Almost", sessions=[(at(17), at(17, 57))]),
        Attendance("Left early", sessions=[(at(17), at(17, 30))]),
    )
    roster = build_roster([meeting], Rules(require_present_at_end=True, end_grace_minutes=5))
    assert person(roster, "Stayed").eligible
    assert person(roster, "Almost").eligible
    assert person(roster, "Left early").reason == "left before the end"


def test_time_window_clips_attendance():
    meeting = report(
        Attendance("Setup crew", sessions=[(at(16), at(17, 5))]),
        Attendance("Audience", sessions=[(at(17, 10), at(18))]),
        start=at(16), end=at(18),
    )
    rules = Rules(window=(time(17, 0), time(17, 45)), min_minutes=10)
    roster = build_roster([meeting], rules)
    assert round(person(roster, "Setup crew").minutes) == 5
    assert not person(roster, "Setup crew").eligible
    assert round(person(roster, "Audience").minutes) == 35


def test_classic_list_counts_open_sessions_until_window_end():
    meeting = Meeting("l", KIND_EVENT_LOG, [Attendance("Still here", sessions=[(at(17, 5), None)])],
                      first_seen=at(17), last_seen=at(17, 10))
    assert round(build_roster([meeting]).people[0].minutes) == 5
    windowed = build_roster([meeting], Rules(window=(time(17), time(18))))
    assert round(windowed.people[0].minutes) == 55
    assert windowed.people[0].present_at_end is True


def test_minimum_meetings_attended():
    first = report(Attendance("Regular", "r@example.com", sessions=[(at(17), at(18))]),
                   Attendance("Once", "o@example.com", sessions=[(at(17), at(18))]))
    start, end = DAY.replace(day=10, hour=17), DAY.replace(day=10, hour=18)
    second = Meeting("b", KIND_REPORT, [Attendance("Regular", "r@example.com", sessions=[(start, end)])],
                     start=start, end=end)
    roster = build_roster([first, second], Rules(min_meetings=2))
    assert person(roster, "Regular").eligible
    assert person(roster, "Once").reason == "qualified in 1 of 2 meetings"


def test_exclusions_by_person_list_and_role():
    meeting = Meeting("a", KIND_LIST, [
        Attendance("Host Person", "host@example.com", role="Organizer"),
        Attendance("Past Winner", "winner@example.com"),
        Attendance("Doe, Jane"),
        Attendance("Lucky Guest"),
    ])
    roster = build_roster([meeting])
    guest_key = person(roster, "Lucky Guest").key
    rules = Rules(exclude_roles=frozenset({"organizer"}), exclude_terms=("WINNER@example.com", "jane doe"),
                  exclude_people=frozenset({guest_key}))
    roster = build_roster([meeting], rules)
    assert person(roster, "Host Person").reason == "role: Organizer"
    assert person(roster, "Past Winner").reason == "on the exclusion list"
    assert person(roster, "Jane Doe").reason == "on the exclusion list"
    assert person(roster, "Lucky Guest").reason == "excluded by the host"
    assert roster.eligible == []


def test_time_rules_do_not_apply_to_plain_lists():
    meeting = Meeting("a", KIND_LIST, [Attendance("Walk-in")])
    roster = build_roster([meeting], Rules(min_minutes=30, require_present_at_end=True))
    assert roster.people[0].eligible
    assert roster.notes
