"""Tables and downloadable files (CSV and Excel)."""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

import pandas as pd

from .draw import Round
from .models import Meeting
from .people import Roster

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe(value: object) -> object:
    """Neutralize spreadsheet formulas coming from uploaded data (CSV/Excel injection)."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _sanitize(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    for column in frame.columns:
        if frame[column].dtype == object or pd.api.types.is_string_dtype(frame[column]):
            frame[column] = frame[column].map(_safe)
    return frame


def to_csv(frame: pd.DataFrame) -> bytes:
    return _sanitize(frame).to_csv(index=False).encode("utf-8-sig")


def to_excel(sheets: Dict[str, pd.DataFrame]) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            _sanitize(frame).to_excel(writer, sheet_name=name[:31], index=False)
            sheet = writer.sheets[name[:31]]
            for column_cells in sheet.columns:
                width = max((len(str(cell.value)) for cell in column_cells if cell.value is not None), default=8)
                sheet.column_dimensions[column_cells[0].column_letter].width = min(max(10, width + 2), 60)
    return buffer.getvalue()


def local_time(moment: datetime, timezone_name: Optional[str]) -> datetime:
    if timezone_name:
        try:
            from zoneinfo import ZoneInfo

            return moment.astimezone(ZoneInfo(timezone_name))
        except Exception:  # noqa: BLE001 - unknown zone or missing tz database
            pass
    return moment.astimezone(timezone.utc)


def _minutes(value: Optional[float]) -> Optional[int]:
    return None if value is None else int(round(value))


def participants_frame(roster: Roster, meetings: Sequence[Meeting], include_email: bool = True) -> pd.DataFrame:
    rows = []
    for person in roster.people:
        row = {
            "Eligible": person.eligible,
            "Name": person.name,
            "Email": person.email,
            "Role": ", ".join(person.roles),
            "Meetings": person.meetings,
            "Qualifying meetings": person.qualifying_meetings,
            "Minutes": _minutes(person.minutes),
            "At the end": person.present_at_end,
            "Not eligible because": "" if person.eligible else person.reason,
            "Also appears as": "; ".join(person.aliases),
        }
        if len(meetings) > 1:
            row["Attended"] = "; ".join(meetings[stat.meeting].label for stat in person.stats)
        if not include_email:
            row.pop("Email")
        rows.append(row)
    return pd.DataFrame(rows)


def winners_frame(rounds: Sequence[Round], timezone_name: Optional[str] = None,
                  include_email: bool = True) -> pd.DataFrame:
    rows = []
    for draw in rounds:
        drawn_at = local_time(draw.drawn_at, timezone_name).strftime("%Y-%m-%d %H:%M:%S %Z")
        for position, winner in enumerate(draw.winners, start=1):
            row = {
                "Round": draw.number,
                "Prize": draw.prize,
                "Position": position,
                "Winner": winner.name,
                "Email": winner.email,
                "No-show": winner.key in draw.no_shows,
                "Drawn at": drawn_at,
                "Draw ID": draw.draw_id,
            }
            if not include_email:
                row.pop("Email")
            rows.append(row)
    columns = ["Round", "Prize", "Position", "Winner", "Email", "No-show", "Drawn at", "Draw ID"]
    if not include_email:
        columns.remove("Email")
    return pd.DataFrame(rows, columns=columns)


def audit_frame(rounds: Sequence[Round], timezone_name: Optional[str] = None) -> pd.DataFrame:
    rows = [{
        "Round": draw.number,
        "Draw ID": draw.draw_id,
        "Prize": draw.prize,
        "Drawn at": local_time(draw.drawn_at, timezone_name).strftime("%Y-%m-%d %H:%M:%S %Z"),
        "Drawn at (UTC)": draw.drawn_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "Eligible pool": len(draw.pool),
        "Winners": len(draw.winners),
        "Chances": "weighted by qualifying meetings" if draw.weighted else "equal",
        "Seed": str(draw.seed),
        "Pool SHA-256": draw.fingerprint,
    } for draw in rounds]
    return pd.DataFrame(rows)


def pools_frame(rounds: Sequence[Round]) -> pd.DataFrame:
    rows = []
    for draw in rounds:
        winners = {winner.key: position for position, winner in enumerate(draw.winners, start=1)}
        for entry in draw.pool:
            rows.append({
                "Round": draw.number,
                "Draw key": entry.key,
                "Name": entry.name,
                "Email": entry.email,
                "Tickets": entry.tickets,
                "Won (position)": winners.get(entry.key),
            })
    return pd.DataFrame(rows, columns=["Round", "Draw key", "Name", "Email", "Tickets", "Won (position)"])


def results_workbook(rounds: Sequence[Round], roster: Optional[Roster], meetings: Sequence[Meeting],
                     timezone_name: Optional[str] = None) -> bytes:
    sheets: Dict[str, pd.DataFrame] = {
        "Winners": winners_frame(rounds, timezone_name),
        "Audit": audit_frame(rounds, timezone_name),
        "Pools": pools_frame(rounds),
    }
    if roster is not None:
        sheets["Participants"] = participants_frame(roster, meetings)
    if meetings:
        sheets["Files"] = files_frame(meetings)
    return to_excel(sheets)


def _clock(moment: Optional[datetime]) -> str:
    return moment.strftime("%H:%M") if moment else ""


def files_frame(meetings: Sequence[Meeting]) -> pd.DataFrame:
    rows: List[dict] = []
    for meeting in meetings:
        rows.append({
            "File": meeting.source,
            "Detected as": meeting.kind,
            "Title": meeting.title,
            "Date": meeting.day.isoformat() if meeting.day else "",
            "From": _clock(meeting.start or meeting.first_seen),
            "To": _clock(meeting.end or meeting.last_seen),
            "People": len(meeting.attendees),
            "Notes": " ".join(meeting.warnings),
        })
    return pd.DataFrame(rows)
