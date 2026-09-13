"""Data structures shared across the app."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

KIND_REPORT = "Attendance report"      # one row per person/session with join and leave times
KIND_EVENT_LOG = "Attendance log"      # one row per join/leave event (classic Teams list)
KIND_LIST = "Participant list"         # names only, no timing information

Session = Tuple[datetime, Optional[datetime]]  # (joined, left); ``left`` is None while still connected

ENGAGEMENT_KINDS = ("reactions", "camera", "raised_hands", "unmutes", "chat")
ENGAGEMENT_LABELS = {
    "reactions": "Reactions",
    "camera": "Camera on",
    "raised_hands": "Raised hands",
    "unmutes": "Unmutes",
    "chat": "Chat messages",
}


@dataclass
class Attendance:
    """One person in one meeting, exactly as found in a file."""

    name: str
    email: str = ""
    role: str = ""
    sessions: List[Session] = field(default_factory=list)
    reported_duration: Optional[timedelta] = None
    engagement: Dict[str, int] = field(default_factory=dict)              # kind -> count
    engagement_events: List[Tuple[datetime, str]] = field(default_factory=list)  # (when, kind)
    mentions: int = 1  # rows naming this person in a list (e.g. two prizes won in a winners file)


@dataclass
class Meeting:
    source: str
    kind: str
    attendees: List[Attendance]
    title: str = ""
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    warnings: List[str] = field(default_factory=list)
    engagement_tracked: bool = False  # the file had reaction/camera/hand/unmute data (even if all zero)

    @property
    def has_timing(self) -> bool:
        return any(a.sessions or a.reported_duration is not None for a in self.attendees)

    @property
    def day(self) -> Optional[date]:
        moment = self.start or self.first_seen
        return moment.date() if moment else None

    @property
    def label(self) -> str:
        parts = [self.title or self.source]
        if self.day:
            parts.append(self.day.isoformat())
        return " · ".join(parts)


@dataclass
class LoadResult:
    source: str
    meetings: List[Meeting] = field(default_factory=list)
    error: str = ""
