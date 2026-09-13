"""Locale tolerant parsing of timestamps and durations found in meeting exports.

Exports differ by language, region and app version, e.g. ``7/16/26, 4:49:08 PM``,
``13/09/2022, 5:30:21 PM``, ``05/09/2022 16:29``, ``16.07.2026 16:49`` or ISO 8601.
Day/month order is inferred from the whole column set, because a single value such as
``05/09/2022`` is ambiguous on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional

from .text import clean_cell, normalize_label

AUTO = "auto"
DAY_FIRST = "day_first"
MONTH_FIRST = "month_first"
DATE_ORDERS = (AUTO, DAY_FIRST, MONTH_FIRST)

_AM = ("am", "a m", "午前", "上午", "오전")
_PM = ("pm", "p m", "午後", "下午", "오후")

_DATETIME_RE = re.compile(
    r"""^\s*
    (?P<d1>\d{1,4})\s*(?P<sep>[./-])\s*(?P<d2>\d{1,2})\s*[./-]\s*(?P<d3>\d{1,4})\.?
    (?:\s*(?:,|T|\s)\s*
        (?P<H>\d{1,2})\s*[:h]\s*(?P<M>\d{2})
        (?:\s*:\s*(?P<S>\d{2})(?:[.,](?P<frac>\d{1,6})\d*)?)?
        \s*(?P<ampm>[ap]\.?\s?m\.?|午前|午後|上午|下午|오전|오후)?
    )?
    \s*(?:Z|UTC|GMT|[+-]\d{2}:?\d{2})?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_TIME_RE = re.compile(
    r"""^\s*(?P<H>\d{1,2})\s*[:h]\s*(?P<M>\d{2})(?:\s*:\s*(?P<S>\d{2})(?:[.,]\d+)?)?
    \s*(?P<ampm>[ap]\.?\s?m\.?|午前|午後|上午|下午|오전|오후)?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_CLOCK_DURATION_RE = re.compile(r"^\s*(?:(\d+)\s*:\s*)?(\d{1,2})\s*:\s*(\d{2})(?:[.,]\d+)?\s*$")
_UNIT_DURATION_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*([^\d\s.,:]+)")

_HOUR_UNITS = ("h", "hr", "hrs", "hour", "hours", "hora", "horas", "std", "stunde", "stunden", "heure", "heures",
               "ora", "ore", "uur", "godz", "saat", "時間", "小时", "시간")
_MINUTE_UNITS = ("m", "min", "mins", "minute", "minutes", "minuto", "minutos", "minuten", "minuti", "mn", "minuut",
                 "dk", "分", "分钟", "분")
_SECOND_UNITS = ("s", "sec", "secs", "second", "seconds", "seg", "segundo", "segundos", "sek", "sekunde",
                 "sekunden", "seconde", "secondes", "secondi", "sn", "秒", "초")


@dataclass(frozen=True)
class _DateTimeParts:
    d1: int
    d2: int
    d3: int
    d1_digits: int
    sep: str
    hour: int
    minute: int
    second: int
    micro: int
    ampm: str  # "", "am" or "pm"
    has_time: bool


def _ampm(value: Optional[str]) -> str:
    if not value:
        return ""
    label = normalize_label(value) or value
    if label in _AM or value in _AM:
        return "am"
    if label in _PM or value in _PM:
        return "pm"
    return ""


def _split_datetime(value: str) -> Optional[_DateTimeParts]:
    match = _DATETIME_RE.match(value)
    if not match:
        return None
    frac = match.group("frac") or ""
    return _DateTimeParts(
        d1=int(match.group("d1")),
        d2=int(match.group("d2")),
        d3=int(match.group("d3")),
        d1_digits=len(match.group("d1")),
        sep=match.group("sep"),
        hour=int(match.group("H") or 0),
        minute=int(match.group("M") or 0),
        second=int(match.group("S") or 0),
        micro=int(frac.ljust(6, "0")) if frac else 0,
        ampm=_ampm(match.group("ampm")),
        has_time=match.group("H") is not None,
    )


def infer_date_order(values: Iterable[object]) -> str:
    """Guess whether ambiguous numeric dates are day-first or month-first."""
    day_votes = month_votes = 0
    dotted = twelve_hour = twenty_four_hour = False

    for value in values:
        parts = _split_datetime(clean_cell(value))
        if parts is None or parts.d1_digits == 4:
            continue
        if parts.d1 > 12 >= parts.d2:
            day_votes += 1
        elif parts.d2 > 12 >= parts.d1:
            month_votes += 1
        dotted = dotted or parts.sep == "."
        twelve_hour = twelve_hour or bool(parts.ampm)
        twenty_four_hour = twenty_four_hour or parts.hour > 12

    if day_votes != month_votes:
        return DAY_FIRST if day_votes > month_votes else MONTH_FIRST
    if dotted:
        return DAY_FIRST
    if twelve_hour:
        return MONTH_FIRST
    if twenty_four_hour:
        return DAY_FIRST
    return MONTH_FIRST


def _to_24h(hour: int, ampm: str) -> int:
    if ampm == "pm" and hour < 12:
        return hour + 12
    if ampm == "am" and hour == 12:
        return 0
    return hour


def _build(year: int, month: int, day: int, parts: _DateTimeParts) -> Optional[datetime]:
    if year < 100:
        year += 2000
    try:
        return datetime(year, month, day, _to_24h(parts.hour, parts.ampm), parts.minute, parts.second, parts.micro)
    except ValueError:
        return None


def parse_datetime(value: object, order: str = MONTH_FIRST) -> Optional[datetime]:
    """Parse a timestamp. ``order`` resolves ambiguous numeric dates (see :func:`infer_date_order`)."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _from_excel_serial(float(value))

    text = clean_cell(value)
    if not text:
        return None

    parts = _split_datetime(text)
    if parts is not None:
        if parts.d1_digits == 4:
            return _build(parts.d1, parts.d2, parts.d3, parts)
        day_first = order == DAY_FIRST
        candidates = [(parts.d2, parts.d1), (parts.d1, parts.d2)] if day_first else \
            [(parts.d1, parts.d2), (parts.d2, parts.d1)]
        for month, day in candidates:
            parsed = _build(parts.d3, month, day, parts)
            if parsed is not None:
                return parsed
        return None

    try:
        return _from_excel_serial(float(text.replace(",", ".")))
    except ValueError:
        pass
    return _parse_with_dateutil(text, order)


def _from_excel_serial(serial: float) -> Optional[datetime]:
    if 20000 <= serial <= 80000:  # 1954..2119, anything else is not a plausible spreadsheet date
        return datetime(1899, 12, 30) + timedelta(days=serial)
    return None


def _parse_with_dateutil(text: str, order: str) -> Optional[datetime]:
    if not any(ch.isalpha() for ch in text) or not any(ch.isdigit() for ch in text):
        return None
    try:
        from dateutil import parser as date_parser
    except ImportError:  # pragma: no cover - dateutil ships with pandas
        return None
    try:
        parsed = date_parser.parse(text, dayfirst=order == DAY_FIRST, fuzzy=False)
    except (ValueError, OverflowError, TypeError):
        return None
    return parsed.replace(tzinfo=None)


def parse_time_of_day(value: object) -> Optional[time]:
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    if isinstance(value, datetime):
        return value.time()
    match = _TIME_RE.match(clean_cell(value))
    if not match:
        return None
    try:
        return time(_to_24h(int(match.group("H")), _ampm(match.group("ampm"))), int(match.group("M")),
                    int(match.group("S") or 0))
    except ValueError:
        return None


def parse_duration(value: object, default_unit_seconds: int = 60) -> Optional[timedelta]:
    """Parse ``1h 13m 10s``, ``1 h 5 min``, ``01:13:10`` or a bare number (minutes by default)."""
    if isinstance(value, timedelta):
        return value
    if isinstance(value, time):
        return timedelta(hours=value.hour, minutes=value.minute, seconds=value.second)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return timedelta(seconds=float(value) * default_unit_seconds)

    text = clean_cell(value).lower()
    if not text:
        return None

    clock = _CLOCK_DURATION_RE.match(text)
    if clock:
        hours, minutes, seconds = clock.groups()
        if hours is None:  # mm:ss
            return timedelta(minutes=int(minutes), seconds=int(seconds))
        return timedelta(hours=int(hours), minutes=int(minutes), seconds=int(seconds))

    try:
        return timedelta(seconds=float(text.replace(",", ".")) * default_unit_seconds)
    except ValueError:
        pass

    total = 0.0
    found = False
    for amount, unit in _UNIT_DURATION_RE.findall(text):
        unit = unit.strip(".").lower()
        number = float(amount.replace(",", "."))
        if unit in _HOUR_UNITS:
            total += number * 3600
        elif unit in _MINUTE_UNITS:
            total += number * 60
        elif unit in _SECOND_UNITS:
            total += number
        else:
            continue
        found = True
    return timedelta(seconds=total) if found else None
