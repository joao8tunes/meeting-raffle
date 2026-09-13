"""Synthetic sample files that mimic real exports. Every name here is fictional (example.com emails)."""

from __future__ import annotations

import codecs
import io
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Tuple

FIRST_NAMES = [
    "Avery", "Blake", "Casey", "Dakota", "Emery", "Finley", "Harper", "Jordan", "Kai", "Logan", "Morgan", "Noa",
    "Parker", "Quinn", "Reese", "Riley", "Rowan", "Sage", "Skyler", "Taylor", "Alex", "Cameron", "Drew", "Eden",
    "Hayden", "Jamie", "Jules", "Lee", "Marley", "Robin", "Sasha", "Toni", "André", "Inês", "Joaquín", "Zoë",
    "Chloé", "Mateo", "Aiko", "Nia",
]
LAST_NAMES = [
    "Nimbus", "Quasar", "Pixel", "Comet", "Falcon", "Maple", "Orbit", "Cedar", "Harbor", "Meadow", "Summit",
    "Willow", "Ember", "Nova", "Pebble", "Raven", "Solstice", "Tide", "Vector", "Zephyr", "Aurora", "Birch",
    "Cobalt", "Delta", "Echo", "Fjord", "Glacier", "Horizon", "Indigo", "Juniper", "Kestrel", "Lumen", "Marble",
    "Nebula", "Onyx", "Prism", "Quill", "Ridge", "Sierra", "Topaz",
]


@dataclass(frozen=True)
class SamplePerson:
    first: str
    last: str
    organizer: bool = False
    guest: bool = False

    @property
    def full(self) -> str:
        return f"{self.first} {self.last}"

    @property
    def directory_name(self) -> str:  # how corporate directories often list people
        return f"{self.last}, {self.first}"

    @property
    def email(self) -> str:
        if self.guest:
            return ""
        ascii_name = f"{self.first}.{self.last}".lower()
        for accented, plain in (("é", "e"), ("ê", "e"), ("í", "i"), ("ë", "e"), ("ó", "o")):
            ascii_name = ascii_name.replace(accented, plain)
        return f"{ascii_name}@example.com"


def sample_people() -> List[SamplePerson]:
    people = [SamplePerson(first, last) for first, last in zip(FIRST_NAMES, LAST_NAMES)]
    people[0] = SamplePerson(people[0].first, people[0].last, organizer=True)
    people[1] = SamplePerson(people[1].first, people[1].last, organizer=True)
    people[-1] = SamplePerson(people[-1].first, people[-1].last, guest=True)
    return people


Session = Tuple[datetime, datetime]


def _sessions(rng: random.Random, person: SamplePerson, start: datetime, end: datetime) -> List[Session]:
    minutes = (end - start).total_seconds() / 60
    if person.organizer:
        return [(start - timedelta(minutes=8, seconds=rng.randint(0, 50)), end)]
    roll = rng.random()
    joined = start + timedelta(seconds=rng.randint(-300, 600))
    if roll < 0.55:  # stays until the end
        return [(joined, end)]
    if roll < 0.70:  # leaves early
        return [(joined, start + timedelta(minutes=minutes * rng.uniform(0.3, 0.7)))]
    if roll < 0.82:  # connection dropped and came back
        drop = start + timedelta(minutes=minutes * rng.uniform(0.25, 0.6))
        back = drop + timedelta(minutes=rng.uniform(1, 5))
        return [(joined, drop), (back, end)]
    if roll < 0.92:  # quick visit
        return [(joined, joined + timedelta(minutes=rng.uniform(1, 8)))]
    return [(joined + timedelta(minutes=minutes * 0.4), end)]  # arrives late


def _meeting(rng: random.Random, day: datetime, attendance: float, reach: int = 40) -> Tuple[datetime, datetime, dict]:
    """``reach`` limits the meeting to the first people of the list, so later editions bring newcomers."""
    start = day.replace(hour=17, minute=0, second=0)
    end = start + timedelta(minutes=60, seconds=rng.randint(0, 500))
    schedule = {}
    for index, person in enumerate(sample_people()):
        if index >= reach and not person.guest:
            continue
        if person.organizer or rng.random() < attendance:
            schedule[person] = _sessions(rng, person, start, end)
    return start, end, schedule


def _duration(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    hours, minutes, seconds = total // 3600, total % 3600 // 60, total % 60
    parts = [f"{hours}h"] if hours else []
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def _utf16(lines: List[str]) -> bytes:
    return codecs.BOM_UTF16_LE + "\r\n".join(lines).encode("utf-16-le")


ENGAGEMENT_TYPES = ("applause", "like", "love", "camera", "hand", "unmute")


def _engagement(rng: random.Random, person: SamplePerson, sessions: List[Session]) -> List[Tuple[datetime, str]]:
    """Fictional reactions, camera, raised hands and unmutes spread over the person's connected time."""
    connected = sum((e - s).total_seconds() for s, e in sessions)
    if connected < 300 or (not person.organizer and rng.random() < 0.3):
        return []
    energy = 2.5 if person.organizer else rng.uniform(0.3, 1.6)
    counts = {
        "applause": int(rng.random() * 4 * energy), "like": int(rng.random() * 2 * energy),
        "love": int(rng.random() * 1.5 * energy), "camera": int(rng.random() * 2 * energy),
        "hand": 1 if rng.random() < 0.12 * energy else 0, "unmute": int(rng.random() * 2.2 * energy),
    }
    events = []
    for kind, count in counts.items():
        for _ in range(count):
            joined, left = rng.choice(sessions)
            events.append((joined + (left - joined) * rng.uniform(0.05, 0.95), kind))
    return sorted(events)


def _teams_report(labels: dict, title: str, day: datetime, seed: int, fmt: Callable[[datetime], str],
                  roles: dict, attendance: float = 0.8, reach: int = 40) -> bytes:
    rng = random.Random(seed)
    start, end, schedule = _meeting(rng, day, attendance=attendance, reach=reach)
    first_join = min(s for sessions in schedule.values() for s, _ in sessions)
    last_leave = max(e for sessions in schedule.values() for _, e in sessions)
    durations = {p: sum((e - s for s, e in sessions), timedelta()) for p, sessions in schedule.items()}
    average = sum(durations.values(), timedelta()) / len(durations)
    engagement = {p: _engagement(rng, p, sessions) for p, sessions in schedule.items()}

    def name(person: SamplePerson) -> str:
        return f"{person.full} ({labels['guest']})" if person.guest else person.full

    def role(person: SamplePerson) -> str:
        return roles["organizer"] if person.organizer else roles["attendee"]

    lines = [
        labels["summary"],
        f"{labels['title']}\t{title}",
        f"{labels['attended']}\t{len(schedule)}",
        f"{labels['start']}\t{fmt(first_join)}",
        f"{labels['end']}\t{fmt(last_leave)}",
        f"{labels['duration']}\t{_duration(last_leave - first_join)}",
        f"{labels['average']}\t{_duration(average)}",
        "",
        labels["participants"],
        "\t".join(labels["participants_header"] + [labels["engagement_columns"][t] for t in ENGAGEMENT_TYPES]),
    ]
    ordered = sorted(schedule, key=lambda p: (not p.organizer, schedule[p][0][0]))
    for person in ordered:
        sessions = schedule[person]
        kinds = [kind for _, kind in engagement[person]]
        counts = [str(kinds.count(t)) if kinds.count(t) else "" for t in ENGAGEMENT_TYPES]
        lines.append("\t".join([name(person), fmt(sessions[0][0]), fmt(sessions[-1][1]), _duration(durations[person]),
                                person.email, person.email, role(person)] + counts))
    lines += ["", labels["activities"], "\t".join(labels["activities_header"])]
    for person in ordered:
        for joined, left in schedule[person]:
            lines.append("\t".join([name(person), fmt(joined), fmt(left), _duration(left - joined), person.email,
                                    role(person)]))
    lines += ["", labels["engagement"], "\t".join(labels["engagement_header"])]
    for when, person, kind in sorted((when, p, kind) for p in ordered for when, kind in engagement[p]):
        lines.append("\t".join([name(person), labels["engagement_values"][kind], fmt(when)]))
    return _utf16(lines)


_EN_LABELS = {
    "summary": "1. Summary", "title": "Meeting title", "attended": "Attended participants",
    "start": "Start time", "end": "End time", "duration": "Meeting duration",
    "average": "Average attendance time", "participants": "2. Participants",
    "participants_header": ["Name", "First Join", "Last Leave", "In-Meeting Duration", "Email",
                            "Participant ID (UPN)", "Role"],
    "activities": "3. In-Meeting Activities",
    "activities_header": ["Name", "Join Time", "Leave Time", "Duration", "Email", "Role"],
    "engagement": "4. Meeting Engagement",
    "engagement_header": ["Name", "Engagement Type", "Time"],
    "engagement_columns": {
        "applause": "Engagement: Reaction - Applause", "like": "Engagement: Reaction - Like",
        "love": "Engagement: Reaction - Love", "camera": "Engagement: Camera On",
        "hand": "Engagement: Raise Hand", "unmute": "Engagement: Unmute",
    },
    "engagement_values": {
        "applause": 'Reaction sent "applause"', "like": 'Reaction sent "like"', "love": 'Reaction sent "love"',
        "camera": "Turned camera on", "hand": "Raised hand", "unmute": "Unmuted",
    },
    "guest": "Guest",
}


def _us_format(moment: datetime) -> str:
    return f"{moment.month}/{moment.day}/{moment:%y}, {moment:%I:%M:%S %p}".replace(", 0", ", ")


def teams_report_en(seed: int = 7) -> bytes:
    return _teams_report(_EN_LABELS, "Cloud Cost Basics", datetime(2026, 3, 3), seed, _us_format,
                         {"organizer": "Organizer", "attendee": "Attendee"}, attendance=0.85, reach=28)


def teams_report_en_april(seed: int = 41) -> bytes:
    return _teams_report(_EN_LABELS, "Security Quick Wins", datetime(2026, 4, 14), seed, _us_format,
                         {"organizer": "Organizer", "attendee": "Attendee"}, attendance=0.7, reach=40)


def teams_report_pt(seed: int = 11) -> bytes:
    labels = {
        "summary": "1. Resumo", "title": "Título da reunião", "attended": "Participantes Atendidos",
        "start": "Hora de início", "end": "Hora de término", "duration": "Duração da reunião",
        "average": "Tempo médio de participação", "participants": "2. Participantes",
        "participants_header": ["Nome", "Primeira Entrada", "Última Saída", "Duração da Reunião", "Email",
                                "ID do participante (UPN)", "Função"],
        "activities": "3. Atividades em Reunião",
        "activities_header": ["Nome", "Horário de Entrada", "Horário de Saída", "Duração", "Email", "Função"],
        "engagement": "4. Compromisso da Reunião",
        "engagement_header": ["Nome", "Tipo de Compromisso", "Tempo"],
        "engagement_columns": {
            "applause": "Envolvimento: Reação - Aplauso", "like": "Envolvimento: Reação - Curtir",
            "love": "Envolvimento: Reação - Amor", "camera": "Compromisso: Câmera Ligada",
            "hand": "Compromisso: Levantar as mãos", "unmute": "Compromisso: Desativar Mudo",
        },
        "engagement_values": {
            "applause": 'Reação enviada "aplausos"', "like": 'Reação enviada "curtir"',
            "love": 'Reação enviada "amor"', "camera": "Ativou a câmera", "hand": "Mãos levantadas",
            "unmute": "Mudo desativado",
        },
        "guest": "Convidado",
    }
    return _teams_report(labels, "Introdução a Dados", datetime(2026, 3, 17), seed,
                         lambda d: d.strftime("%d/%m/%Y %H:%M:%S"),
                         {"organizer": "Organizador", "attendee": "Participante"}, attendance=0.8, reach=33)


def teams_attendance_list_classic(seed: int = 23) -> bytes:
    """Classic "meetingAttendanceList.csv": one row per join/leave event, no emails, no end time."""
    rng = random.Random(seed)
    start, end, schedule = _meeting(rng, datetime(2026, 3, 31), attendance=0.75, reach=37)
    list_started = start - timedelta(minutes=2)
    events = []
    for person, sessions in schedule.items():
        name = person.directory_name if rng.random() < 0.8 else person.full
        if person.guest:
            name = f"{person.full} (Guest)"
        for index, (joined, left) in enumerate(sessions):
            action = "Joined before" if index == 0 and joined <= list_started else "Joined"
            events.append((max(joined, list_started), name, action))
            if left < end:
                events.append((left, name, "Left"))
    events.sort()

    def fmt(moment: datetime) -> str:
        return f"{moment:%d/%m/%Y}, {moment:%I:%M:%S %p}".replace(", 0", ", ")

    return _utf16(["Full Name\tUser Action\tTimestamp"] + [f"{n}\t{a}\t{fmt(t)}" for t, n, a in events])


def checkin_list_txt() -> bytes:
    """A plain list typed by hand at an in-person event."""
    people = sample_people()[5:30]
    lines = [person.directory_name if index % 4 == 0 else person.full for index, person in enumerate(people)]
    return ("\n".join(f"{i}. {line}" for i, line in enumerate(lines, start=1)) + "\n").encode("utf-8")


def registrations_xlsx() -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Registrations"
    sheet.append(["First name", "Last name", "Email", "Team", "Registered at"])
    rng = random.Random(5)
    teams = ["Platform", "Data", "Design", "Sales", "People"]
    for index, person in enumerate(sample_people()[:32]):
        sheet.append([person.first, person.last, person.email or None, rng.choice(teams),
                      datetime(2026, 2, 20, 9, 0) + timedelta(hours=index * 5)])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def previous_winners_csv() -> bytes:
    """Winners file of an earlier edition, in the same format this app exports."""
    people = sample_people()
    rows = [
        (1, "Conference ticket", 1, people[6], False),
        (1, "Conference ticket", 2, people[13], False),
        (2, "Coffee voucher", 1, people[21], True),
        (2, "Coffee voucher", 2, people[6], False),
    ]
    lines = ["Round,Prize,Position,Winner,Email,No-show,Drawn at,Draw ID"]
    for number, prize, position, person, no_show in rows:
        lines.append(f"{number},{prize},{position},{person.full},{person.email},{no_show},"
                     f"2026-02-17 18:05:00 UTC,SAMPLE{number:02d}")
    return ("\n".join(lines) + "\n").encode("utf-8-sig")


def demo_files() -> List[Tuple[str, bytes]]:
    """Four sessions of an event series, exported in different formats and languages."""
    return [
        ("session-2026-03-03_attendance-report_en.csv", teams_report_en()),
        ("session-2026-03-17_relatorio-de-presenca_pt-BR.csv", teams_report_pt()),
        ("session-2026-03-31_meetingAttendanceList.csv", teams_attendance_list_classic()),
        ("session-2026-04-14_attendance-report_en.csv", teams_report_en_april()),
    ]


def example_files() -> List[Tuple[str, bytes]]:
    return demo_files() + [
        ("session-2026-02_previous-winners.csv", previous_winners_csv()),
        ("event-checkin-list.txt", checkin_list_txt()),
        ("event-registrations.xlsx", registrations_xlsx()),
    ]


def write_examples(folder: Optional[str] = None) -> List[str]:
    from pathlib import Path

    target = Path(folder) if folder else Path(__file__).resolve().parent.parent / "examples"
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in example_files():
        (target / name).write_bytes(data)
        written.append(str(target / name))
    return written


if __name__ == "__main__":
    for path in write_examples():
        print(path)
