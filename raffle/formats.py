"""Turn the rows of an export into a :class:`~raffle.models.Meeting`.

Nothing here depends on a specific vendor or language. Tables are found by recognizing
column headers in several languages (Teams, Zoom, Google Meet, Webex, spreadsheets) and,
when a file has no header at all, by looking at what the columns contain.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from .models import KIND_EVENT_LOG, KIND_LIST, KIND_REPORT, Attendance, Meeting, Session
from .names import email_key, name_key
from .text import clean_cell, looks_like_email, normalize_label
from .timeparse import AUTO, DAY_FIRST, infer_date_order, parse_datetime, parse_duration

# Column roles
NAME, FIRST_NAME, LAST_NAME, EMAIL, UPN, ROLE = "name", "first_name", "last_name", "email", "upn", "role"
ACTION, TIMESTAMP, JOIN, LEAVE, DURATION, DATE = "action", "timestamp", "join", "leave", "duration", "date"
NO_SHOW = "no_show"
KIND_ACTIVITY = "activity"  # reactions/engagement logs: they carry names and times but not attendance

_NAME_LABELS = {
    "full name", "name", "nome", "nome completo", "participant", "participant name", "participante", "nombre",
    "nombre completo", "nom", "nom complet", "nom du participant", "vollstandiger name", "teilnehmer",
    "teilnehmername", "nome e cognome", "nominativo", "partecipante", "attendee", "attendee name", "display name",
    "user", "user name", "username", "naam", "volledige naam", "deelnemer", "imie i nazwisko", "uczestnik",
    "ad soyad", "名前", "氏名", "姓名", "이름", "people", "person", "pessoa", "persona", "winner", "winners",
    "ganhador", "vencedor", "ganador",
}
_FIRST_NAME_LABELS = {
    "first name", "firstname", "given name", "primeiro nome", "prenome", "nome proprio", "prenom", "vorname",
    "primer nombre", "nome di battesimo", "voornaam", "imie",
}
_LAST_NAME_LABELS = {
    "last name", "lastname", "surname", "family name", "sobrenome", "ultimo nome", "apelido", "apellido",
    "apellidos", "nom de famille", "nachname", "familienname", "cognome", "achternaam", "nazwisko",
}
_ACTION_LABELS = {
    "user action", "action", "activity", "atividade", "acao", "acao do usuario", "accion", "accion del usuario",
    "actividad", "activite", "action de l utilisateur", "aktion", "benutzeraktion", "aktivitat", "azione",
    "azione utente", "attivita", "actie", "activiteit", "gebruikersactie", "event", "evento",
}
_TIMESTAMP_LABELS = {
    "timestamp", "time stamp", "carimbo de data hora", "data e hora", "data hora", "date time", "date and time",
    "fecha y hora", "marca de tiempo", "marca de hora", "horodatage", "date et heure", "zeitstempel",
    "datum uhrzeit", "data e ora", "data ora", "tijdstempel", "time", "tempo", "hora", "horario", "heure", "zeit",
    "ora", "tijd", "datetime",
}
_DATE_LABELS = {"date", "data", "fecha", "datum", "meeting date", "data da reuniao", "fecha de la reunion"}
_EMAIL_WORDS = {"email", "emails", "mail", "correo", "courriel"}
_UPN_WORDS = {"upn", "upns"}
_JOIN_WORDS = {
    "join", "joined", "joins", "joining", "entrada", "entrou", "ingresso", "ingreso", "ingressou", "union", "unio",
    "arrivee", "arrival", "beitritt", "beitrittszeit", "deelname", "toegetreden", "dolaczenia", "katilma",
}
_LEAVE_WORDS = {
    "leave", "left", "leaves", "leaving", "exit", "exited", "saida", "salida", "depart", "departure", "verlassen",
    "verlassenszeit", "austritt", "uscita", "vertrek", "opuszczenia", "ayrilma",
}
_DURATION_WORDS = {"duration", "duracao", "duracion", "duree", "dauer", "durata", "duur", "minutes", "minutos"}
_ROLE_WORDS = {"role", "roles", "funcao", "funcion", "rol", "rolle", "ruolo", "papel", "fonction"}
_NAME_WORDS = {"name", "nome", "nombre", "naam", "nom", "namen"}
_NOT_A_PERSON_WORDS = {"meeting", "reuniao", "reunion", "title", "titulo", "titre", "id", "file", "arquivo", "event"}

_TITLE_KEYS = {
    "meeting title", "titulo da reuniao", "titulo de la reunion", "titre de la reunion", "besprechungstitel",
    "titolo della riunione", "titel van vergadering", "meeting name", "topic", "title", "titulo", "titre", "titel",
    "titolo", "subject", "assunto", "asunto", "sujet", "betreff", "oggetto", "nome da reuniao",
    "nombre de la reunion", "event name", "nome do evento",
}
_START_WORDS = {"start", "inicio", "debut", "beginn", "inizio", "begintijd", "starttijd", "startzeit", "comienzo"}
_END_WORDS = {"end", "termino", "fim", "final", "fin", "ende", "endzeit", "fine", "einde", "eindtijd",
              "encerramento", "finalizacao"}

_JOIN_ACTION_WORDS = {
    "joined", "join", "ingressou", "entrou", "ingreso", "unio", "unido", "rejoint", "beigetreten", "teilgenommen",
    "unito", "entrato", "partecipato", "toegetreden", "dolaczyl", "katildi", "connected", "conectado",
    "conectou", "entered", "arrived",
}
_LEAVE_ACTION_WORDS = {
    "left", "leave", "saiu", "deixou", "salio", "abandono", "quitte", "parti", "partie", "verlassen", "uscito",
    "uscita", "abbandonato", "vertrokken", "opuscil", "ayrildi", "exited", "disconnected", "desconectado",
    "desconectou", "departed",
}

_NO_SHOW_LABELS = {"no show", "noshow", "no shows", "nao compareceu", "ausente", "did not claim", "unclaimed"}
_TRUTHY = {"true", "yes", "y", "1", "x", "sim", "si", "oui", "ja", "verdadeiro", "wahr", "vero"}

# Engagement (reactions, camera, raised hands, unmute, chat) as found in attendance report columns such as
# "Engagement: Camera On" / "Compromisso: Câmera Ligada" and in engagement logs ("Reaction sent", "Unmuted").
_CAMERA_WORDS = {"camera", "cameras", "camara", "camaras", "kamera", "fotocamera", "videocamera", "webcam", "video"}
_HAND_WORDS = {"hand", "hands", "mao", "maos", "mano", "manos", "hande", "handen", "raise", "raised", "levantar",
               "levantada", "levantadas", "levantou", "lever", "levee", "alzata", "alzate", "alzare", "opsteken",
               "opgestoken", "heben", "gehoben"}
_UNMUTE_WORDS = {"unmute", "unmuted", "unmutes", "unmuting"}
_MUTE_WORDS = {"mudo", "mute", "silenciar", "silencio", "stumm", "stummschaltung", "muet", "muto", "dempen"}
_OFF_WORDS = {"desativar", "desativado", "desativou", "desactivar", "desactivado", "desactivo", "quitar", "aufheben",
              "aufgehoben", "retirer", "desattivare", "disattiva", "opheffen", "off"}
_MIC_WORDS = {"microfone", "microfono", "microphone", "micro", "mic", "mikrofon", "microfoon", "audio", "som",
              "sonido", "son", "ton"}
_ON_WORDS = {"on", "ligado", "ligada", "ligou", "ativado", "ativou", "activado", "activo", "active", "activer",
             "attivato", "attiva", "aan", "ein", "eingeschaltet", "reactivar", "reativar", "riattiva", "retablir"}
_REACTION_WORDS = {
    "reaction", "reactions", "reacao", "reacoes", "reaccion", "reacciones", "reaktion", "reaktionen", "reazione",
    "reazioni", "reactie", "reacties", "applause", "aplauso", "aplausos", "applaudissements", "applaus",
    "applausi", "laugh", "gargalhada", "risa", "rire", "lachen", "risata", "like", "curtir", "gusta", "jaime",
    "gefallt", "piace", "love", "amor", "coracao", "corazon", "adore", "herz", "cuore", "surprised", "surpreso",
    "sorprendido", "surpris", "uberrascht", "sorpreso", "verrast", "celebrate", "comemorar", "emoji",
}
_CHAT_WORDS = {"chat", "message", "messages", "mensagem", "mensagens", "mensaje", "mensajes", "nachricht",
               "nachrichten", "messaggio", "messaggi", "bericht", "berichten"}

MEETING_GAP = timedelta(hours=6)  # activity separated by a longer pause belongs to different meetings

_SECTION_TITLE_RE = re.compile(r"^\s*\d{1,2}\s*[.)]\s+\S")
_AMBIGUOUS_DATE_RE = re.compile(r"^\s*(\d{1,2})[./-](\d{1,2})[./-]\d{2,4}")
_INTEGER_RE = re.compile(r"^\s*\d+(?:[.,]0+)?\s*$")


def engagement_kind(label: object) -> Optional[str]:
    """Map an engagement column title or log entry to a kind in :data:`~raffle.models.ENGAGEMENT_KINDS`."""
    words = set(normalize_label(label).split())
    if not words:
        return None
    muted_off = words & _MUTE_WORDS and words & _OFF_WORDS
    microphone_on = words & _MIC_WORDS and words & _ON_WORDS
    if words & _UNMUTE_WORDS or muted_off or microphone_on:
        return "unmutes"
    if words & _CAMERA_WORDS:
        return "camera"
    if words & _HAND_WORDS:
        return "raised_hands"
    if words & _REACTION_WORDS:
        return "reactions"
    if words & _CHAT_WORDS:
        return "chat"
    return None


def column_role(label: object) -> Optional[str]:
    text = normalize_label(label)
    if not text:
        return None
    words = set(text.split())

    if text in _NO_SHOW_LABELS:
        return NO_SHOW
    if words & _UPN_WORDS or text.startswith("participant id") or "id do participante" in text:
        return UPN
    if words & _EMAIL_WORDS or "e mail" in text:
        return EMAIL
    if text in _FIRST_NAME_LABELS:
        return FIRST_NAME
    if text in _LAST_NAME_LABELS:
        return LAST_NAME
    if text in _NAME_LABELS:
        return NAME
    if text in _ACTION_LABELS:
        return ACTION
    if words & _JOIN_WORDS:
        return JOIN
    if words & _LEAVE_WORDS:
        return LEAVE
    if words & _DURATION_WORDS:
        return DURATION
    if words & _ROLE_WORDS:
        return ROLE
    if text in _TIMESTAMP_LABELS:
        return TIMESTAMP
    if text in _DATE_LABELS:
        return DATE
    if words & _NAME_WORDS and not words & _NOT_A_PERSON_WORDS:
        return NAME
    return None


_ROLE_SYNONYMS = {
    "Organizer": {"organizer", "organiser", "organizador", "organizadora", "organisateur", "organisatrice",
                  "organisator", "organizzatore", "organizzatrice", "organizator"},
    "Co-organizer": {"co organizer", "coorganizer", "co organiser", "coorganizador", "co organizador",
                     "coorganisateur", "co organisateur", "mitorganisator", "co organizzatore", "coorganizzatore"},
    "Presenter": {"presenter", "apresentador", "apresentadora", "presentador", "presentadora", "presentateur",
                  "presentatrice", "referent", "referentin", "relatore", "relatrice", "presentator"},
    "Attendee": {"attendee", "participant", "participante", "teilnehmer", "teilnehmerin", "partecipante",
                 "deelnemer", "uczestnik", "guest", "convidado", "invitado", "invite", "gast", "ospite"},
}
_ROLE_LOOKUP = {label: role for role, labels in _ROLE_SYNONYMS.items() for label in labels}


def canonical_role(value: object) -> str:
    """Translate meeting roles to English ("Organizador" -> "Organizer"); unknown roles are kept as-is."""
    text = clean_cell(value)
    return _ROLE_LOOKUP.get(normalize_label(text), text)


def is_header_label(value: object) -> bool:
    """True for cells that are clearly column titles ("Name", "E-mail", "Nome completo"...), not people."""
    text = normalize_label(value)
    return text in _NAME_LABELS or text in _FIRST_NAME_LABELS or text in _LAST_NAME_LABELS \
        or text in {"email", "e mail", "email address", "e mail address"}


def classify_action(value: object) -> str:
    """Return ``"join"``, ``"leave"`` or ``""`` for a join/leave event label in any supported language."""
    words = set(normalize_label(value).split())
    if words & _LEAVE_ACTION_WORDS:
        return "leave"
    if words & _JOIN_ACTION_WORDS:
        return "join"
    return ""


@dataclass
class Table:
    columns: Dict[str, int]
    rows: List[List[str]]
    header: List[str] = field(default_factory=list)
    title: str = ""

    @property
    def kind(self) -> str:
        if JOIN in self.columns:
            return KIND_REPORT
        if ACTION in self.columns and TIMESTAMP in self.columns:
            return KIND_EVENT_LOG
        if TIMESTAMP in self.columns:
            return KIND_ACTIVITY
        return KIND_LIST

    def cell(self, row: Sequence[str], role: str) -> str:
        index = self.columns.get(role)
        return row[index] if index is not None and index < len(row) else ""

    def header_of(self, role: str) -> str:
        index = self.columns.get(role)
        return normalize_label(self.header[index]) if index is not None and index < len(self.header) else ""


@dataclass
class Summary:
    title: str = ""
    start: str = ""
    end: str = ""


def _is_blank(row: Sequence[str]) -> bool:
    return not any(row)


def _header_columns(row: Sequence[str]) -> Optional[Dict[str, int]]:
    columns: Dict[str, int] = {}
    for index, cell in enumerate(row):
        role = column_role(cell)
        if role and role not in columns:
            columns[role] = index
    if NAME not in columns and FIRST_NAME in columns and LAST_NAME not in columns and len(columns) > 1:
        columns[NAME] = columns.pop(FIRST_NAME)
    if NAME in columns or (FIRST_NAME in columns and LAST_NAME in columns):
        return columns
    return None


def find_tables(rows: List[List[str]]) -> List[Table]:
    tables: List[Table] = []
    title = ""
    index = 0
    while index < len(rows):
        row = rows[index]
        index += 1
        if _is_blank(row):
            continue
        columns = _header_columns(row)
        if columns is None:
            non_empty = [cell for cell in row if cell]
            if len(non_empty) == 1:
                title = non_empty[0]
            continue

        data: List[List[str]] = []
        while index < len(rows) and not _is_blank(rows[index]):
            candidate = rows[index]
            is_section_title = _SECTION_TITLE_RE.match(candidate[0]) and sum(1 for c in candidate if c) == 1
            is_new_header = sum(1 for cell in candidate if column_role(cell)) >= 2 and _header_columns(candidate)
            if is_section_title or is_new_header:
                break
            data.append(candidate)
            index += 1
        tables.append(Table(columns=columns, rows=data, header=list(row), title=title))
        title = ""
    return tables


def find_summary(rows: List[List[str]], order: str) -> Summary:
    """Read key/value rows such as ``Meeting title | ...`` or ``Hora de início | ...``."""
    summary = Summary()
    for row in rows:
        cells = [cell for cell in row if cell]
        if len(cells) != 2:
            continue
        key, value = normalize_label(cells[0]), cells[1]
        words = set(key.split())
        if not summary.title and key in _TITLE_KEYS:
            summary.title = value
        elif not summary.start and words & _START_WORDS and not words & _JOIN_WORDS and parse_datetime(value, order):
            summary.start = value
        elif not summary.end and words & _END_WORDS and not words & _LEAVE_WORDS and parse_datetime(value, order):
            summary.end = value
    return summary


def infer_headerless_table(rows: List[List[str]]) -> Optional[Table]:
    """Recognize files without a header row by looking at the column contents."""
    data = [row for row in rows if sum(1 for cell in row if cell) >= 2]
    if not data:
        return None
    width = max(len(row) for row in data)
    order = infer_date_order(cell for row in data for cell in row)

    def share(column: int, predicate: Callable[[str], object]) -> float:
        values = [row[column] for row in data if column < len(row) and row[column]]
        return sum(1 for value in values if predicate(value)) / len(values) if values else 0.0

    actions = [c for c in range(width) if share(c, classify_action) >= 0.6]
    stamps = [c for c in range(width) if c not in actions and share(c, lambda v: parse_datetime(v, order)) >= 0.6]
    emails = [c for c in range(width) if share(c, looks_like_email) >= 0.6]
    used = set(actions) | set(stamps) | set(emails)
    names = [c for c in range(width) if c not in used and share(c, lambda v: any(ch.isalpha() for ch in v)) >= 0.6]
    if not names:
        return None

    columns = {NAME: names[0]}
    if emails:
        columns[EMAIL] = emails[0]
    if actions and stamps:
        columns.update({ACTION: actions[0], TIMESTAMP: stamps[0]})
    elif len(stamps) >= 2:
        columns.update({JOIN: stamps[0], LEAVE: stamps[1]})
    elif not emails:
        return None  # a plain list is handled by the loader, which keeps commas inside names
    return Table(columns=columns, rows=data)


def _person_name(table: Table, row: Sequence[str]) -> str:
    name = clean_cell(table.cell(row, NAME))
    first, last = clean_cell(table.cell(row, FIRST_NAME)), clean_cell(table.cell(row, LAST_NAME))
    if not name and (first or last):
        name = f"{first} {last}".strip()
    elif name and last and not normalize_label(name).endswith(normalize_label(last)):
        name = f"{name} {last}"
    if not name:
        email = clean_cell(table.cell(row, EMAIL)) or clean_cell(table.cell(row, UPN))
        name = email if looks_like_email(email) else ""
    return name if name_key(name) else ""


def _identity(name: str, email: str) -> str:
    return f"email:{email}" if email else f"name:{name_key(name)}"


def _fix_session(joined: datetime, left: Optional[datetime]) -> Session:
    if left is not None and left < joined:
        if left.date() == joined.date():
            left += timedelta(days=1)  # clock times crossed midnight
        else:
            joined, left = left, joined
    return joined, left


def events_to_sessions(events: List[Tuple[datetime, int, str]], list_start: Optional[datetime]) -> List[Session]:
    """Pair join/leave events. Parallel connections (e.g. phone + laptop) count once."""
    sessions: List[Session] = []
    connections = 0
    opened: Optional[datetime] = None
    for stamp, _, action in sorted(events):
        if action == "join":
            if connections == 0:
                opened = stamp
            connections += 1
        elif connections > 0:
            connections -= 1
            if connections == 0 and opened is not None:
                sessions.append((opened, stamp))
                opened = None
        elif not sessions:  # left without a recorded join: was already there when the list started
            sessions.append((list_start or stamp, stamp))
    if connections > 0 and opened is not None:
        sessions.append((opened, None))
    return sessions


def _is_truthy(value: str) -> bool:
    return normalize_label(value) in _TRUTHY


def _engagement_columns(table: Table) -> Dict[int, str]:
    """Columns such as "Engagement: Camera On" that hold per-person counts."""
    used = set(table.columns.values())
    columns = {}
    for index, label in enumerate(table.header):
        kind = engagement_kind(label) if index not in used else None
        values = [row[index] for row in table.rows if index < len(row) and row[index]]
        if kind and all(_INTEGER_RE.match(value) for value in values):
            columns[index] = kind
    return columns


class _MeetingBuilder:
    def __init__(self, order: str):
        self.order = order
        self.records: Dict[str, Attendance] = {}
        self.log_events: Dict[str, List[Tuple[datetime, int, str]]] = {}
        self.stamps: List[datetime] = []
        self.skipped_rows = 0
        self.engagement_tracked = False
        self._column_counts: Dict[str, Counter] = {}
        self._logged_counts: Dict[str, Counter] = {}
        self._by_name: Dict[str, Set[str]] = {}
        self._position = 0

    def _time(self, value: str) -> Optional[datetime]:
        parsed = parse_datetime(value, self.order) if value else None
        if parsed is not None:
            self.stamps.append(parsed)
        return parsed

    def _record(self, table: Table, row: Sequence[str], create: bool = True) -> Optional[Tuple[str, Attendance]]:
        name = _person_name(table, row)
        if not name:
            return None
        email = email_key(table.cell(row, EMAIL)) or email_key(table.cell(row, UPN))
        key = _identity(name, email)
        record = self.records.get(key)
        if record is None and not email:
            # e.g. an engagement log without emails pointing at people listed with an email
            same_name = self._by_name.get(name_key(name), set())
            if len(same_name) == 1:
                key = next(iter(same_name))
                record = self.records[key]
        if record is None:
            if not create:
                return None
            record = self.records[key] = Attendance(name=name, email=email)
            self._by_name.setdefault(name_key(name), set()).add(key)
        record.role = record.role or canonical_role(table.cell(row, ROLE))
        return key, record

    def _add_engagement_counts(self, table: Table, row: Sequence[str], key: str, columns: Dict[int, str]) -> None:
        counts = self._column_counts.setdefault(key, Counter())
        for index, kind in columns.items():
            if index < len(row) and row[index]:
                counts[kind] += int(float(row[index].replace(",", ".")))

    def add_list(self, table: Table) -> None:
        columns = _engagement_columns(table)
        self.engagement_tracked = self.engagement_tracked or bool(columns)
        for row in table.rows:
            if _is_truthy(table.cell(row, NO_SHOW)):
                continue
            known = len(self.records)
            found = self._record(table, row)
            if found and len(self.records) == known:
                found[1].mentions += 1
            if found and columns:
                self._add_engagement_counts(table, row, found[0], columns)

    def add_report(self, table: Table, timed_keys: Optional[Set[str]] = None) -> None:
        """Add a join/leave table. Sessions are skipped for people in ``timed_keys`` (already timed)."""
        in_seconds = any(unit in table.header_of(DURATION) for unit in ("sec", "seg", "sek"))
        columns = _engagement_columns(table)
        self.engagement_tracked = self.engagement_tracked or bool(columns)
        for row in table.rows:
            found = self._record(table, row)
            if found is None:
                continue
            key, record = found
            joined = self._time(table.cell(row, JOIN))
            left = self._time(table.cell(row, LEAVE))
            if joined is None:
                if table.cell(row, JOIN):
                    self.skipped_rows += 1
            elif timed_keys is None or key not in timed_keys:
                record.sessions.append(_fix_session(joined, left))
            if record.reported_duration is None and table.cell(row, DURATION):
                record.reported_duration = parse_duration(table.cell(row, DURATION), 1 if in_seconds else 60)
            if columns:
                self._add_engagement_counts(table, row, key, columns)

    def add_event_log(self, table: Table) -> None:
        for row in table.rows:
            found = self._record(table, row)
            if found is None:
                continue
            action = classify_action(table.cell(row, ACTION))
            stamp = self._time(table.cell(row, TIMESTAMP))
            self._position += 1
            if not action or stamp is None:
                self.skipped_rows += 1
                continue
            self.log_events.setdefault(found[0], []).append((stamp, self._position, action))

    def add_engagement_log(self, table: Table) -> None:
        """Engagement logs ("Reaction sent", "Unmuted"...) only enrich people already in the meeting."""
        used = set(table.columns.values())
        width = max((len(row) for row in table.rows), default=0)
        kind_column = None
        for index in range(width):
            values = [row[index] for row in table.rows if index < len(row) and row[index]]
            if index not in used and values and sum(1 for v in values if engagement_kind(v)) / len(values) >= 0.5:
                kind_column = index
                break
        if kind_column is None:
            return
        self.engagement_tracked = True
        for row in table.rows:
            kind = engagement_kind(row[kind_column]) if kind_column < len(row) else None
            found = self._record(table, row, create=False) if kind else None
            if found is None:
                continue
            key, record = found
            self._logged_counts.setdefault(key, Counter())[kind] += 1
            stamp = parse_datetime(table.cell(row, TIMESTAMP), self.order)
            if stamp is not None:
                record.engagement_events.append((stamp, kind))

    def finish_engagement(self) -> None:
        # Reports can list the same engagement twice (per-person totals and a detailed log): keep the larger count.
        for key, record in self.records.items():
            totals = Counter()
            for source in (self._column_counts.get(key, Counter()), self._logged_counts.get(key, Counter())):
                for kind, count in source.items():
                    totals[kind] = max(totals[kind], count)
            record.engagement = {kind: count for kind, count in totals.items() if count > 0}
            record.engagement_events.sort()


def time_ranges(stamps: List[datetime], gap: timedelta = MEETING_GAP) -> List[Tuple[datetime, datetime]]:
    """Group timestamps into separate meetings when there is a long pause between them."""
    ranges: List[Tuple[datetime, datetime]] = []
    for stamp in sorted(stamps):
        if ranges and stamp - ranges[-1][1] <= gap:
            ranges[-1] = (ranges[-1][0], stamp)
        else:
            ranges.append((stamp, stamp))
    return ranges


def _split_meetings(builder: _MeetingBuilder, kind: str, summary: Summary, base: Meeting) -> List[Meeting]:
    summary_start = parse_datetime(summary.start, builder.order) if summary.start else None
    summary_end = parse_datetime(summary.end, builder.order) if summary.end else None
    stamps = builder.stamps + [moment for moment in (summary_start, summary_end) if moment]
    ranges = time_ranges(stamps) if kind != KIND_LIST else []
    if not ranges:
        base.attendees = list(builder.records.values())
        return [base] if base.attendees else []

    meetings = []
    engagement_placed = set()
    for index, (first, last) in enumerate(ranges):
        def inside(moment: Optional[datetime]) -> bool:
            return moment is not None and first <= moment <= last

        attendees = []
        for key, record in builder.records.items():
            if kind == KIND_EVENT_LOG:
                events = [event for event in builder.log_events.get(key, []) if inside(event[0])]
                sessions = events_to_sessions(events, first)
            else:
                sessions = [session for session in record.sessions if inside(session[0])]
            has_any_timing = bool(builder.log_events.get(key)) or bool(record.sessions)
            if not (sessions or (index == 0 and not has_any_timing)):
                continue
            attendance = Attendance(record.name, record.email, record.role, sessions, record.reported_duration)
            if len(ranges) == 1:
                attendance.engagement = dict(record.engagement)
                attendance.engagement_events = list(record.engagement_events)
            else:
                attendance.engagement_events = [e for e in record.engagement_events if inside(e[0])]
                if attendance.engagement_events:
                    attendance.engagement = dict(Counter(k for _, k in attendance.engagement_events))
                elif key not in engagement_placed:
                    attendance.engagement = dict(record.engagement)
            if attendance.engagement:
                engagement_placed.add(key)
            attendees.append(attendance)
        if not attendees:
            continue
        meeting = Meeting(
            source=base.source, kind=kind, attendees=attendees, title=base.title,
            start=summary_start if inside(summary_start) else None,
            end=summary_end if inside(summary_end) else None,
            first_seen=first, last_seen=last, warnings=list(base.warnings),
            engagement_tracked=base.engagement_tracked,
        )
        meetings.append(meeting)

    if len(meetings) > 1:
        for meeting in meetings:
            meeting.warnings.append(
                f"This file has activity from {len(meetings)} separate meetings (long pause between them), "
                "so it was split into separate meetings."
            )
    return meetings


def interpret_rows(rows: List[List[str]], source: str, date_order: str = AUTO) -> Tuple[List[Meeting], int]:
    """Interpret parsed rows. Returns the meetings found and a confidence level from 0 to 3."""
    tables = [table for table in find_tables(rows) if table.rows]
    confidence = 3
    if not tables:
        inferred = infer_headerless_table(rows)
        if inferred is None:
            return [], 0
        tables, confidence = [inferred], 2

    time_values = [table.cell(row, role) for table in tables for row in table.rows for role in (JOIN, LEAVE, TIMESTAMP)]
    time_values = [value for value in time_values if value]
    order = date_order
    if date_order == AUTO:
        order = infer_date_order(time_values or [cell for row in rows for cell in row[1:2]])
    summary = find_summary(rows, order)

    by_kind: Dict[str, List[Table]] = {}
    for table in tables:
        by_kind.setdefault(table.kind, []).append(table)

    builder = _MeetingBuilder(order)
    if KIND_REPORT in by_kind:
        kind = KIND_REPORT
        # The most detailed table (e.g. "In-Meeting Activities", one row per session) provides the timing;
        # the others (e.g. the "Participants" overview) only fill in people and details that are missing.
        primary, *others = sorted(by_kind[KIND_REPORT], key=lambda t: len(t.rows), reverse=True)
        builder.add_report(primary)
        timed = {key for key, record in builder.records.items() if record.sessions}
        for table in others:
            builder.add_report(table, timed_keys=timed)
    elif KIND_EVENT_LOG in by_kind:
        kind = KIND_EVENT_LOG
        for table in by_kind[KIND_EVENT_LOG]:
            builder.add_event_log(table)
    else:
        kind = KIND_LIST
        for table in by_kind.get(KIND_LIST) or by_kind.get(KIND_ACTIVITY, []):
            builder.add_list(table)
    if kind != KIND_LIST or KIND_LIST in by_kind:
        for table in by_kind.get(KIND_ACTIVITY, []):
            builder.add_engagement_log(table)
    builder.finish_engagement()

    titles = [t.title for t in tables if t.title and not _SECTION_TITLE_RE.match(t.title)]
    base = Meeting(source=source, kind=kind, attendees=[],
                   title=clean_cell(summary.title) or (clean_cell(titles[0]) if titles else ""),
                   engagement_tracked=builder.engagement_tracked)
    if builder.skipped_rows:
        base.warnings.append(f"{builder.skipped_rows} row(s) with unreadable times or actions were ignored.")
    if date_order == AUTO and _only_ambiguous_dates(time_values):
        reading = "day/month" if order == DAY_FIRST else "month/day"
        base.warnings.append(
            f"Dates such as 05/06 are ambiguous and were read as {reading}. You can change this under Advanced."
        )

    meetings = _split_meetings(builder, kind, summary, base)
    for meeting in meetings:
        still_connected = any(s[1] is None for a in meeting.attendees for s in a.sessions)
        if kind == KIND_EVENT_LOG and meeting.end is None and still_connected:
            stamp = meeting.last_seen.strftime("%H:%M") if meeting.last_seen else "?"
            meeting.warnings.append(
                "This list does not say when the meeting ended, so people still connected are counted until the "
                f"last recorded activity ({stamp}). Set a time window for precise minutes."
            )
    return meetings, confidence if meetings else 0


def _only_ambiguous_dates(values: List[str]) -> bool:
    ambiguous = unambiguous = 0
    for value in values:
        match = _AMBIGUOUS_DATE_RE.match(value)
        if match:
            if int(match.group(1)) > 12 or int(match.group(2)) > 12:
                unambiguous += 1
            else:
                ambiguous += 1
    return ambiguous > 0 and unambiguous == 0

