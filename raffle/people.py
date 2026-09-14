"""Merge attendance from several meetings into people and decide who is eligible for the draw."""

from __future__ import annotations

import hashlib
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from .models import Meeting, Session
from .names import display_name, email_key, name_key
from .text import normalize_label, strip_accents


@dataclass(frozen=True)
class Rules:
    min_minutes: float = 0
    require_present_at_end: bool = False
    end_grace_minutes: float = 5
    window: Optional[Tuple[time, time]] = None
    min_meetings: int = 1
    exclude_roles: FrozenSet[str] = frozenset()
    exclude_people: FrozenSet[str] = frozenset()
    exclude_terms: Tuple[str, ...] = ()
    merge_similar_names: bool = False
    similarity: int = 92
    reorder_names: bool = True


@dataclass
class MeetingStats:
    meeting: int
    minutes: Optional[float]         # None when the file has no timing information
    present_at_end: Optional[bool]   # None when unknown
    qualifies: bool
    reason: str = ""
    intervals: List[Tuple[datetime, datetime]] = field(default_factory=list)  # connected time, window-clipped
    engagement: Dict[str, int] = field(default_factory=dict)


@dataclass
class Person:
    key: str
    name: str
    emails: List[str] = field(default_factory=list)
    roles: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    stats: List[MeetingStats] = field(default_factory=list)
    eligible: bool = False
    reason: str = ""

    @property
    def email(self) -> str:
        return self.emails[0] if self.emails else ""

    @property
    def meetings(self) -> int:
        return len(self.stats)

    @property
    def qualifying_meetings(self) -> int:
        return sum(1 for stat in self.stats if stat.qualifies)

    @property
    def minutes(self) -> Optional[float]:
        values = [stat.minutes for stat in self.stats if stat.minutes is not None]
        return sum(values) if values else None

    @property
    def present_at_end(self) -> Optional[bool]:
        values = [stat.present_at_end for stat in self.stats if stat.present_at_end is not None]
        return any(values) if values else None


@dataclass
class Roster:
    people: List[Person]
    merged_names: List[Tuple[str, str]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def eligible(self) -> List[Person]:
        return [person for person in self.people if person.eligible]

    @property
    def roles(self) -> List[str]:
        seen: Dict[str, str] = {}
        for person in self.people:
            for role in person.roles:
                seen.setdefault(normalize_label(role), role)
        return sorted(seen.values(), key=sort_key)


def sort_key(value: str) -> str:
    return strip_accents(value).casefold()


class _UnionFind:
    def __init__(self, size: int):
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a: int, b: int) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[max(root_a, root_b)] = min(root_a, root_b)


def _group_records(records: List[Tuple[int, str, str]], rules: Rules) -> Tuple[List[List[int]], List[Tuple[str, str]]]:
    """Group (meeting, name, email) records that belong to the same person.

    Emails are the strongest identity. Records without an email join the person with the same name,
    unless that name is shared by people with different emails (namesakes stay apart).
    """
    groups = _UnionFind(len(records))
    by_email: Dict[str, int] = {}
    by_name: Dict[str, List[int]] = {}
    for index, (_, name, email) in enumerate(records):
        if email:
            if email in by_email:
                groups.union(by_email[email], index)
            else:
                by_email[email] = index
        by_name.setdefault(name_key(name), []).append(index)

    for indexes in by_name.values():
        emails = {records[i][2] for i in indexes if records[i][2]}
        # Namesakes with different emails stay apart; records without email can't be attributed to
        # one of them, so they form their own person.
        same_person = indexes if len(emails) <= 1 else [i for i in indexes if not records[i][2]]
        for index in same_person[1:]:
            groups.union(same_person[0], index)

    merged: List[Tuple[str, str]] = []
    if rules.merge_similar_names:
        merged = _merge_similar(records, groups, rules.similarity)

    clusters: Dict[int, List[int]] = {}
    for index in range(len(records)):
        clusters.setdefault(groups.find(index), []).append(index)
    return list(clusters.values()), merged


def _merge_similar(records: List[Tuple[int, str, str]], groups: _UnionFind, threshold: int) -> List[Tuple[str, str]]:
    """Merge people whose names are nearly identical (typos, missing accents) and whose emails don't conflict."""
    representatives: Dict[int, Tuple[str, set, str]] = {}
    for index, (_, name, email) in enumerate(records):
        root = groups.find(index)
        key, emails, label = representatives.get(root, (name_key(name), set(), display_name(name)))
        if email:
            emails.add(email)
        representatives[root] = (key, emails, label)

    roots = list(representatives)
    keys = [representatives[root][0] for root in roots]
    merged = []
    for i, j in _similar_pairs(keys, threshold):
        emails_i, emails_j = representatives[roots[i]][1], representatives[roots[j]][1]
        if emails_i and emails_j and not emails_i & emails_j:
            continue
        if groups.find(roots[i]) != groups.find(roots[j]):
            groups.union(roots[i], roots[j])
            merged.append((representatives[roots[i]][2], representatives[roots[j]][2]))
    return merged


_PAIRS_CACHE: "OrderedDict[str, List[Tuple[int, int]]]" = OrderedDict()
_PAIRS_BLOCK = 512


def _similar_pairs(keys: List[str], threshold: int) -> List[Tuple[int, int]]:
    """Index pairs (i < j) of similar names.

    Compared block by block so memory stays small for very large lists (a full matrix of 10,000 names would
    need hundreds of MB). Results are cached by a digest of the input, so the cache holds indexes, not names.
    """
    digest = hashlib.sha256("\x1f".join([str(threshold), *keys]).encode("utf-8")).hexdigest()
    if digest in _PAIRS_CACHE:
        _PAIRS_CACHE.move_to_end(digest)
        return _PAIRS_CACHE[digest]

    try:
        from rapidfuzz import fuzz, process
    except ImportError:  # pragma: no cover - rapidfuzz is a declared dependency
        from difflib import SequenceMatcher

        pairs = [(i, j) for i in range(len(keys)) for j in range(i + 1, len(keys))
                 if SequenceMatcher(None, keys[i], keys[j]).ratio() * 100 >= threshold]
    else:
        import numpy as np

        pairs = []
        for start in range(0, len(keys), _PAIRS_BLOCK):
            block = keys[start:start + _PAIRS_BLOCK]
            scores = process.cdist(block, keys[start:], scorer=fuzz.token_sort_ratio, score_cutoff=threshold,
                                   dtype=np.uint8, workers=-1)
            rows, columns = scores.nonzero()
            pairs.extend((start + int(i), start + int(j)) for i, j in zip(rows, columns) if int(j) > int(i))

    _PAIRS_CACHE[digest] = pairs
    while len(_PAIRS_CACHE) > 8:
        _PAIRS_CACHE.popitem(last=False)
    return pairs


def _merge_intervals(intervals: Iterable[Tuple[datetime, datetime]]) -> List[Tuple[datetime, datetime]]:
    merged: List[Tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def window_bounds(meeting: Meeting, window: Optional[Tuple[time, time]]) -> Optional[Tuple[datetime, datetime]]:
    if window is None or meeting.day is None:
        return None
    start = datetime.combine(meeting.day, window[0])
    end = datetime.combine(meeting.day, window[1])
    if end <= start:
        end += timedelta(days=1)
    return start, end


def event_span(meeting: Meeting, window: Optional[Tuple[time, time]]) -> Tuple[Optional[datetime], Optional[datetime]]:
    """The period statistics refer to: the time window when set, otherwise the meeting itself."""
    bounds = window_bounds(meeting, window)
    if bounds:
        return bounds
    return meeting.start or meeting.first_seen, meeting.end or meeting.last_seen


def effective_intervals(meeting: Meeting, sessions: Sequence[Session],
                        window: Optional[Tuple[time, time]] = None) -> List[Tuple[datetime, datetime]]:
    """Connected periods with open sessions closed and overlaps merged, clipped to the time window."""
    bounds = window_bounds(meeting, window)
    still_connected_until = meeting.end or (bounds[1] if bounds else None) or meeting.last_seen

    intervals = []
    for joined, left in sessions:
        effective_left = left if left is not None else (still_connected_until or joined)
        intervals.append((joined, max(joined, effective_left)))
    intervals = _merge_intervals(intervals)
    if bounds:
        intervals = [(max(s, bounds[0]), min(e, bounds[1])) for s, e in intervals]
        intervals = [(s, e) for s, e in intervals if e > s]
    return intervals


def meeting_stats(meeting: Meeting, index: int, sessions: Sequence[Session], reported: Optional[timedelta],
                  rules: Rules) -> MeetingStats:
    if not meeting.has_timing:
        return MeetingStats(meeting=index, minutes=None, present_at_end=None, qualifies=True)

    bounds = window_bounds(meeting, rules.window)
    intervals = effective_intervals(meeting, sessions, rules.window)
    seconds = sum((e - s).total_seconds() for s, e in intervals)
    if not sessions and reported is not None:
        seconds = reported.total_seconds()
    minutes = seconds / 60

    present: Optional[bool] = None
    reference_end = bounds[1] if bounds else (meeting.end or meeting.last_seen)
    if sessions and reference_end is not None:
        grace = timedelta(minutes=rules.end_grace_minutes)
        lefts = [left for _, left in sessions]
        present = None in lefts or max(lefts) >= reference_end - grace

    reasons = []
    if bounds and sessions and seconds <= 0:
        reasons.append("not connected during the time window")
    elif rules.min_minutes and minutes < rules.min_minutes:
        reasons.append(f"{minutes:.0f} of {rules.min_minutes:.0f} min")
    if rules.require_present_at_end and present is False:
        reasons.append("left before the end")
    return MeetingStats(meeting=index, minutes=minutes, present_at_end=present, qualifies=not reasons,
                        reason=", ".join(reasons), intervals=intervals)


def _exclusion_matcher(terms: Iterable[str]):
    emails, names = set(), set()
    for term in terms:
        email = email_key(term)
        if email:
            emails.add(email)
        elif name_key(term):
            names.add(name_key(term))

    def matches(person: Person) -> bool:
        if emails & set(person.emails):
            return True
        return any(name_key(value) in names for value in [person.name, *person.aliases])

    return matches


def build_roster(meetings: Sequence[Meeting], rules: Rules = Rules()) -> Roster:
    records: List[Tuple[int, str, str]] = []
    attendances = []
    for meeting_index, meeting in enumerate(meetings):
        for attendance in meeting.attendees:
            records.append((meeting_index, attendance.name, email_key(attendance.email)))
            attendances.append(attendance)

    clusters, merged_names = _group_records(records, rules)
    is_excluded_by_list = _exclusion_matcher(rules.exclude_terms)
    excluded_roles = {normalize_label(role) for role in rules.exclude_roles}
    min_meetings = max(1, min(rules.min_meetings, len(meetings)))

    people = []
    for cluster in clusters:
        names = Counter(display_name(records[i][1], rules.reorder_names) for i in cluster)
        ranked = sorted(names, key=lambda n: ("(" in n, -names[n], -len(n), sort_key(n)))
        emails = Counter(records[i][2] for i in cluster if records[i][2])
        roles = list(dict.fromkeys(attendances[i].role for i in cluster if attendances[i].role))

        per_meeting: Dict[int, Tuple[List[Session], Optional[timedelta]]] = {}
        engagement: Dict[int, Counter] = {}
        for i in cluster:
            sessions, reported = per_meeting.get(records[i][0], ([], None))
            sessions = sessions + attendances[i].sessions
            durations = [d for d in (reported, attendances[i].reported_duration) if d is not None]
            per_meeting[records[i][0]] = (sessions, max(durations) if durations else None)
            engagement.setdefault(records[i][0], Counter()).update(attendances[i].engagement)

        stats = []
        for m, (sessions, duration) in sorted(per_meeting.items()):
            stat = meeting_stats(meetings[m], m, sessions, duration, rules)
            stat.engagement = {kind: count for kind, count in engagement[m].items() if count}
            stats.append(stat)

        email_list = [email for email, _ in emails.most_common()]
        key = f"email:{email_list[0]}" if email_list else f"name:{name_key(ranked[0])}"
        person = Person(key=key, name=ranked[0], emails=email_list, roles=roles, aliases=ranked[1:], stats=stats)

        role_hit = next((role for role in person.roles if normalize_label(role) in excluded_roles), None)
        if person.key in rules.exclude_people:
            person.reason = "excluded by the host"
        elif is_excluded_by_list(person):
            person.reason = "on the exclusion list"
        elif role_hit:
            person.reason = f"role: {role_hit}"
        elif person.qualifying_meetings < min_meetings:
            failed = [stat.reason for stat in person.stats if not stat.qualifies and stat.reason]
            if len(meetings) > 1:
                person.reason = f"qualified in {person.qualifying_meetings} of {min_meetings} meetings"
                if failed and min_meetings == 1:
                    person.reason = failed[0]
            else:
                person.reason = failed[0] if failed else "did not qualify"
        else:
            person.eligible = True
        people.append(person)

    people.sort(key=lambda p: (sort_key(p.name), p.key))
    _disambiguate_keys(people)

    notes = []
    if any(not m.has_timing for m in meetings) and (rules.min_minutes or rules.require_present_at_end or rules.window):
        notes.append("Time-based rules don't apply to plain participant lists: everyone in them qualifies.")
    return Roster(people=people, merged_names=merged_names, notes=notes)


def _disambiguate_keys(people: List[Person]) -> None:
    """Keys must be unique (namesakes without email would otherwise collide)."""
    seen: Counter = Counter()
    for person in people:
        seen[person.key] += 1
        if seen[person.key] > 1:
            person.key = f"{person.key}#{seen[person.key]}"
