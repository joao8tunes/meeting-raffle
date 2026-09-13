from datetime import datetime, time, timedelta

import pytest

from raffle.timeparse import (DAY_FIRST, MONTH_FIRST, infer_date_order, parse_datetime, parse_duration,
                              parse_time_of_day)


@pytest.mark.parametrize("value, order, expected", [
    ("7/16/26, 4:49:08 PM", MONTH_FIRST, datetime(2026, 7, 16, 16, 49, 8)),
    ("13/09/2022, 5:30:21 PM", DAY_FIRST, datetime(2022, 9, 13, 17, 30, 21)),
    ("05/09/22 16:29:58", DAY_FIRST, datetime(2022, 9, 5, 16, 29, 58)),
    ("05/09/2022 16:29", MONTH_FIRST, datetime(2022, 5, 9, 16, 29)),
    ("16.07.2026 16:49", DAY_FIRST, datetime(2026, 7, 16, 16, 49)),
    ("2026-07-16T16:49:08.250Z", MONTH_FIRST, datetime(2026, 7, 16, 16, 49, 8, 250000)),
    ("2026-07-16 16:49:08-03:00", DAY_FIRST, datetime(2026, 7, 16, 16, 49, 8)),
    ("16/07/2026 4:49:08 p. m.", DAY_FIRST, datetime(2026, 7, 16, 16, 49, 8)),
    ("7/16/2026 12:05 AM", MONTH_FIRST, datetime(2026, 7, 16, 0, 5)),
    ("Jul 16, 2026 4:49 PM", MONTH_FIRST, datetime(2026, 7, 16, 16, 49)),
    ("\"7/16/26, 4:49:08 PM\"", MONTH_FIRST, datetime(2026, 7, 16, 16, 49, 8)),
])
def test_parse_datetime(value, order, expected):
    assert parse_datetime(value, order) == expected


def test_wrong_order_falls_back_to_a_valid_date():
    assert parse_datetime("13/09/2022 17:30", MONTH_FIRST) == datetime(2022, 9, 13, 17, 30)


@pytest.mark.parametrize("value", ["", "Joined", "99/99/2022", None, "abc 12"])
def test_parse_datetime_rejects_garbage(value):
    assert parse_datetime(value) is None


def test_excel_serial_and_native_values():
    assert parse_datetime(45123.5) == datetime(2023, 7, 16, 12, 0)
    assert parse_datetime(datetime(2026, 1, 2, 3, 4)) == datetime(2026, 1, 2, 3, 4)


@pytest.mark.parametrize("values, expected", [
    (["13/09/2022 17:30", "05/09/2022 17:31"], DAY_FIRST),
    (["7/16/26, 4:49:08 PM", "7/1/26, 4:49:08 PM"], MONTH_FIRST),
    (["05/09/2022 16:29"], DAY_FIRST),          # 24h clock suggests a non-US locale
    (["05/09/22, 4:29:00 PM"], MONTH_FIRST),    # AM/PM suggests US
    (["05.09.2022 09:00"], DAY_FIRST),
])
def test_infer_date_order(values, expected):
    assert infer_date_order(values) == expected


@pytest.mark.parametrize("value, seconds", [
    ("1h 13m 10s", 4390),
    ("59m 48s", 3588),
    ("8s", 8),
    ("1h 37s", 3637),
    ("1 Std. 13 Min.", 4380),
    ("2 horas e 5 minutos", 7500),
    ("01:13:10", 4390),
    ("45", 2700),
    ("1時間13分", 4380),
])
def test_parse_duration(value, seconds):
    assert parse_duration(value) == timedelta(seconds=seconds)


def test_parse_time_of_day():
    assert parse_time_of_day("5:30 PM") == time(17, 30)
    assert parse_time_of_day("17h45") == time(17, 45)
    assert parse_time_of_day("nope") is None
