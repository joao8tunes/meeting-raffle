"""Attendance insights across meetings: audience, retention, loyalty, engagement and prizes."""

from __future__ import annotations

import bisect
import hashlib
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import PurePath
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .draw import Round
from .models import ENGAGEMENT_KINDS, KIND_EVENT_LOG, Meeting
from .names import email_key, name_key
from .people import MeetingStats, Person, Roster, event_span

MAX_TIMELINE_POINTS = 240


@dataclass(frozen=True)
class ScoreWeights:
    """Relative weight of each component of an event's relevance score."""

    audience: float = 0.4
    retention: float = 0.4
    engagement: float = 0.2


@dataclass
class EventInsight:
    meeting: int                     # index in the loaded meetings
    number: int                      # 1-based chronological position
    label: str                       # unique, e.g. "Mar 03 · Cloud Cost Basics"
    short_label: str                 # unique and compact for chart axes, e.g. "Mar 03"
    title: str
    source: str
    kind: str
    day: Optional[date]
    start: Optional[datetime]
    end: Optional[datetime]
    duration_minutes: Optional[float]
    audience: int
    new_people: int
    returning_people: int
    avg_minutes: Optional[float]
    median_minutes: Optional[float]
    avg_share: Optional[float]       # average share of the event each person attended (0-1)
    stayed_share: Optional[float]    # share still connected at the end (0-1)
    peak: Optional[int]
    peak_at: Optional[datetime]
    engagement_tracked: bool
    engagement: Dict[str, int]
    engaged_people: Optional[int]
    engaged_share: Optional[float]
    timeline: List[Tuple[float, int]]                # (minutes since start, people connected)
    engagement_timeline: List[Tuple[float, str]]     # (minutes since start, kind)
    score: Optional[float] = None
    score_parts: Dict[str, float] = field(default_factory=dict)


@dataclass
class PersonInsight:
    key: str
    name: str
    email: str
    roles: List[str]
    events: int
    attendance_rate: float
    minutes: Optional[float]
    avg_minutes: Optional[float]
    stayed: int
    streak: int
    first_event: str
    last_event: str
    engagement: Dict[str, int]
    prizes_session: int
    prizes_history: int
    eligible: bool
    minutes_by_event: Dict[int, Optional[float]]     # event number -> minutes (None = file without timing)

    @property
    def prizes(self) -> int:
        return self.prizes_session + self.prizes_history

    @property
    def engagement_total(self) -> int:
        return sum(self.engagement.values())


@dataclass
class Overview:
    events: int
    people: int
    attendances: int
    person_hours: Optional[float]
    avg_audience: float
    avg_minutes: Optional[float]
    returning_share: Optional[float]
    avg_events_per_person: float
    stayed_share: Optional[float]
    engaged_share: Optional[float]
    prizes: int
    people_awarded: int
    repeat_winners: int
    winners_avg_events: Optional[float]


@dataclass
class Insights:
    events: List[EventInsight]
    people: List[PersonInsight]
    overview: Overview
    distribution: Dict[int, int]
    unmatched_prizes: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def has_timing(self) -> bool:
        return any(event.duration_minutes is not None for event in self.events)

    @property
    def has_engagement(self) -> bool:
        return any(event.engagement_tracked for event in self.events)

    def event(self, number: int) -> EventInsight:
        return self.events[number - 1]


def _mean(values: Sequence[float]) -> Optional[float]:
    return statistics.fmean(values) if values else None


def chronological(meetings: Sequence[Meeting]) -> List[int]:
    def moment(index: int):
        meeting = meetings[index]
        when = meeting.start or meeting.first_seen
        return (when is None, when or datetime.max, index)

    return sorted(range(len(meetings)), key=moment)


def event_labels(meetings: Sequence[Meeting], order: Sequence[int]) -> Dict[int, Tuple[str, str]]:
    """Unique (full, short) labels such as ("Mar 03 · Cloud Cost Basics", "Mar 03")."""
    labels: Dict[int, Tuple[str, str]] = {}
    used_full: Counter = Counter()
    used_short: Counter = Counter()
    for index in order:
        meeting = meetings[index]
        name = meeting.title or ("Untitled meeting" if meeting.day else PurePath(meeting.source).stem)
        if len(name) > 40:
            name = name[:39] + "…"
        full = f"{meeting.day:%b %d} · {name}" if meeting.day else name
        short = f"{meeting.day:%b %d}" if meeting.day else (name if len(name) <= 14 else name[:13] + "…")
        used_full[full] += 1
        used_short[short] += 1
        labels[index] = (full if used_full[full] == 1 else f"{full} ({used_full[full]})",
                         short if used_short[short] == 1 else f"{short} ({used_short[short]})")
    return labels


def _timeline(intervals: Iterable[Tuple[datetime, datetime]], start: datetime, end: datetime):
    """People connected over time, plus the peak and when it happened."""
    intervals = list(intervals)
    starts = sorted(s for s, _ in intervals)
    ends = sorted(e for _, e in intervals)
    total = (end - start).total_seconds()
    step = max(60.0, total / MAX_TIMELINE_POINTS)
    points = []
    moment_seconds = 0.0
    while moment_seconds <= total + 1e-6:
        moment = start + timedelta(seconds=moment_seconds)
        points.append((moment_seconds / 60, bisect.bisect_right(starts, moment) - bisect.bisect_right(ends, moment)))
        moment_seconds += step

    peak, peak_at, connected = 0, None, 0
    for moment, delta in sorted([(s, 1) for s in starts] + [(e, -1) for e in ends], key=lambda x: (x[0], x[1])):
        connected += delta
        if connected > peak:
            peak, peak_at = connected, moment
    return points, peak, peak_at


def _longest_streak(numbers: Sequence[int]) -> int:
    best = run = 0
    previous = None
    for number in sorted(numbers):
        run = run + 1 if previous is not None and number == previous + 1 else 1
        best = max(best, run)
        previous = number
    return best


def _prize_counts(people: Sequence[Person], rounds: Sequence[Round], history: Sequence[str]):
    session = Counter(winner.key for draw in rounds for winner in draw.winners if winner.key not in draw.no_shows)
    by_email: Dict[str, str] = {}
    by_name: Dict[str, List[str]] = {}
    for person in people:
        for email in person.emails:
            by_email[email] = person.key
        for value in {name_key(person.name), *(name_key(alias) for alias in person.aliases)}:
            by_name.setdefault(value, []).append(person.key)

    past, unmatched = Counter(), 0
    for entry in history:
        email = email_key(entry)
        key = by_email.get(email) if email else None
        if key is None and not email:
            candidates = by_name.get(name_key(entry), [])
            key = candidates[0] if len(candidates) == 1 else None
        if key is None:
            unmatched += 1
        else:
            past[key] += 1
    return session, past, unmatched


def build_insights(meetings: Sequence[Meeting], roster: Roster, window: Optional[Tuple[time, time]] = None,
                   rounds: Sequence[Round] = (), prize_history: Sequence[str] = (),
                   weights: ScoreWeights = ScoreWeights()) -> Insights:
    order = chronological(meetings)
    number_of = {meeting_index: position + 1 for position, meeting_index in enumerate(order)}
    labels = event_labels(meetings, order)

    # Who attended each meeting (people with no time inside the window don't count as audience).
    attendance: Dict[int, List[Tuple[Person, MeetingStats]]] = {index: [] for index in range(len(meetings))}
    for person in roster.people:
        for stat in person.stats:
            if stat.minutes is None or stat.minutes > 0 or window is None:
                attendance[stat.meeting].append((person, stat))

    events: List[EventInsight] = []
    seen: set = set()
    for meeting_index in order:
        meeting = meetings[meeting_index]
        attendees = attendance[meeting_index]
        keys = {person.key for person, _ in attendees}
        start, end = event_span(meeting, window)
        duration = (end - start).total_seconds() / 60 if start and end and end > start else None
        minutes = [stat.minutes for _, stat in attendees if stat.minutes is not None]
        presence = [stat.present_at_end for _, stat in attendees if stat.present_at_end is not None]

        timeline, peak, peak_at = [], None, None
        if meeting.has_timing and start and end and end > start:
            timeline, peak, peak_at = _timeline((iv for _, stat in attendees for iv in stat.intervals), start, end)

        totals = Counter()
        engaged = 0
        for _, stat in attendees:
            totals.update(stat.engagement)
            engaged += 1 if any(stat.engagement.values()) else 0
        engagement_timeline = []
        if start:
            for attendee in meeting.attendees:
                for when, kind in attendee.engagement_events:
                    if (end is None or start <= when <= end):
                        engagement_timeline.append(((when - start).total_seconds() / 60, kind))

        events.append(EventInsight(
            meeting=meeting_index,
            number=number_of[meeting_index],
            label=labels[meeting_index][0],
            short_label=labels[meeting_index][1],
            title=meeting.title,
            source=meeting.source,
            kind=meeting.kind,
            day=meeting.day,
            start=start,
            end=end,
            duration_minutes=duration if meeting.has_timing else None,
            audience=len(attendees),
            new_people=len(keys - seen),
            returning_people=len(keys & seen),
            avg_minutes=_mean(minutes),
            median_minutes=statistics.median(minutes) if minutes else None,
            avg_share=_mean([min(1.0, m / duration) for m in minutes]) if duration and minutes else None,
            stayed_share=_mean([1.0 if p else 0.0 for p in presence]),
            peak=peak,
            peak_at=peak_at,
            engagement_tracked=meeting.engagement_tracked,
            engagement={kind: totals[kind] for kind in ENGAGEMENT_KINDS if totals[kind]},
            engaged_people=engaged if meeting.engagement_tracked else None,
            engaged_share=engaged / len(attendees) if meeting.engagement_tracked and attendees else None,
            timeline=timeline,
            engagement_timeline=sorted(engagement_timeline),
        ))
        seen |= keys

    _score(events, weights)

    session_prizes, past_prizes, unmatched = _prize_counts(roster.people, rounds, prize_history)
    people: List[PersonInsight] = []
    for person in roster.people:
        stats = [stat for stat in person.stats if stat.minutes is None or stat.minutes > 0 or window is None]
        if not stats and not (session_prizes[person.key] or past_prizes[person.key]):
            continue
        numbers = sorted(number_of[stat.meeting] for stat in stats)
        timed = [stat.minutes for stat in stats if stat.minutes is not None]
        engagement = Counter()
        for stat in stats:
            engagement.update(stat.engagement)
        people.append(PersonInsight(
            key=person.key,
            name=person.name,
            email=person.email,
            roles=list(person.roles),
            events=len(stats),
            attendance_rate=len(stats) / len(meetings) if meetings else 0.0,
            minutes=sum(timed) if timed else None,
            avg_minutes=_mean(timed),
            stayed=sum(1 for stat in stats if stat.present_at_end),
            streak=_longest_streak(numbers),
            first_event=labels[order[numbers[0] - 1]][0] if numbers else "",
            last_event=labels[order[numbers[-1] - 1]][0] if numbers else "",
            engagement={kind: engagement[kind] for kind in ENGAGEMENT_KINDS if engagement[kind]},
            prizes_session=session_prizes[person.key],
            prizes_history=past_prizes[person.key],
            eligible=person.eligible,
            minutes_by_event={number_of[stat.meeting]: stat.minutes for stat in stats},
        ))

    return Insights(
        events=events,
        people=people,
        overview=_overview(events, people),
        distribution=dict(sorted(Counter(p.events for p in people if p.events).items())),
        unmatched_prizes=unmatched,
        notes=_notes(meetings, events, window),
    )


def _score(events: List[EventInsight], weights: ScoreWeights) -> None:
    """Relevance = weighted average of audience (vs. the largest event), retention and engagement (0-100)."""
    largest = max((event.audience for event in events), default=0)
    for event in events:
        parts: Dict[str, float] = {}
        if largest:
            parts["audience"] = event.audience / largest
        if event.avg_share is not None:
            parts["retention"] = event.avg_share
        if event.engaged_share is not None:
            parts["engagement"] = event.engaged_share
        weight_of = {"audience": weights.audience, "retention": weights.retention, "engagement": weights.engagement}
        total = sum(weight_of[name] for name in parts)
        event.score_parts = parts
        event.score = 100 * sum(parts[name] * weight_of[name] for name in parts) / total if total > 0 else None


def _overview(events: Sequence[EventInsight], people: Sequence[PersonInsight]) -> Overview:
    attended = [p for p in people if p.events]
    minutes = [m for p in attended for m in p.minutes_by_event.values() if m is not None]
    tracked = [e for e in events if e.engagement_tracked]
    stayed = [e.stayed_share * e.audience for e in events if e.stayed_share is not None]
    stayed_base = [e.audience for e in events if e.stayed_share is not None]
    winners = [p for p in people if p.prizes]
    return Overview(
        events=len(events),
        people=len(attended),
        attendances=sum(e.audience for e in events),
        person_hours=sum(minutes) / 60 if minutes else None,
        avg_audience=_mean([e.audience for e in events]) or 0.0,
        avg_minutes=_mean(minutes),
        returning_share=(sum(1 for p in attended if p.events > 1) / len(attended)) if len(events) > 1 and attended
        else None,
        avg_events_per_person=_mean([p.events for p in attended]) or 0.0,
        stayed_share=sum(stayed) / sum(stayed_base) if sum(stayed_base) else None,
        engaged_share=(sum(e.engaged_people or 0 for e in tracked) / sum(e.audience for e in tracked))
        if tracked and sum(e.audience for e in tracked) else None,
        prizes=sum(p.prizes for p in people),
        people_awarded=len(winners),
        repeat_winners=sum(1 for p in winners if p.prizes > 1),
        winners_avg_events=_mean([p.events for p in winners]),
    )


def _notes(meetings: Sequence[Meeting], events: Sequence[EventInsight], window) -> List[str]:
    notes = []
    if window:
        notes.append(f"Statistics only count the time window {window[0]:%H:%M}–{window[1]:%H:%M}.")
    if not window and any(m.kind == KIND_EVENT_LOG and m.end is None for m in meetings):
        notes.append("Classic Teams lists don't record when meetings ended: their minutes are a lower bound. "
                     "Set a time window in the sidebar for precise numbers.")
    untimed = sum(1 for e in events if e.duration_minutes is None)
    if untimed:
        notes.append(f"{untimed} of {len(events)} events come from lists without join/leave times, so they "
                     "count for audience but not for minutes or retention.")
    tracked = sum(1 for e in events if e.engagement_tracked)
    if 0 < tracked < len(events):
        notes.append(f"Engagement data (reactions, camera, raised hands, unmutes) exists for {tracked} of "
                     f"{len(events)} events; relevance scores of the others ignore engagement.")
    return notes


def pseudonyms(keys: Iterable[str]) -> Dict[str, str]:
    """Stable, non-identifying codes ("Person 007") that don't follow alphabetical order."""
    ordered = sorted(set(keys), key=lambda key: hashlib.sha256(key.encode("utf-8")).hexdigest())
    width = max(3, len(str(len(ordered))))
    return {key: f"Person {index:0{width}d}" for index, key in enumerate(ordered, start=1)}
