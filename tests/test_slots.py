from datetime import date, datetime
from zoneinfo import ZoneInfo

from rooms import TZ
from slots import night_options, slot_label, start_times, times_of_day

NIGHT = "2026-10-30"
BEFORE = datetime(2026, 10, 30, 12, 0, tzinfo=TZ)    # 那天中午，全部時間都還沒過


def room(start, end):
    return {"range_start": start, "range_end": end}


# night_options
def test_night_options_7_days():
    assert night_options(date(2026, 10, 25)) == [
        "2026-10-25", "2026-10-26", "2026-10-27", "2026-10-28",
        "2026-10-29", "2026-10-30", "2026-10-31",
    ]


def test_night_options_cross_month():
    nights = night_options(date(2026, 10, 29))
    assert nights[0] == "2026-10-29"
    assert nights[-1] == "2026-11-04"


# start_times
def test_same_day():
    assert start_times(room("20:00", "22:00"), NIGHT, BEFORE) == [
        "2026-10-30 20:00", "2026-10-30 20:30", "2026-10-30 21:00",
        "2026-10-30 21:30", "2026-10-30 22:00",
    ]


def test_cross_midnight_18_to_02():
    times = start_times(room("18:00", "02:00"), NIGHT, BEFORE)
    assert len(times) == 17
    assert times[0] == "2026-10-30 18:00"
    assert times[-1] == "2026-10-31 02:00"
    assert times[11:14] == ["2026-10-30 23:30", "2026-10-31 00:00", "2026-10-31 00:30"]


def test_cross_midnight_23_to_01():
    assert start_times(room("23:00", "01:00"), NIGHT, BEFORE) == [
        "2026-10-30 23:00", "2026-10-30 23:30", "2026-10-31 00:00",
        "2026-10-31 00:30", "2026-10-31 01:00",
    ]


def test_cross_month_end():
    times = start_times(room("23:30", "00:30"), "2026-10-31", BEFORE)
    assert times == ["2026-10-31 23:30", "2026-11-01 00:00", "2026-11-01 00:30"]


def test_12_hours_is_25():
    times = start_times(room("18:00", "06:00"), NIGHT, BEFORE)
    assert len(times) == 25
    assert times[-1] == "2026-10-31 06:00"


def test_past_excluded():
    now = datetime(2026, 10, 30, 20, 10, tzinfo=TZ)
    times = start_times(room("18:00", "02:00"), NIGHT, now)
    assert times[0] == "2026-10-30 20:30"
    assert "2026-10-30 20:00" not in times


def test_now_exactly_on_slot_kept():
    now = datetime(2026, 10, 30, 20, 0, tzinfo=TZ)
    assert start_times(room("18:00", "02:00"), NIGHT, now)[0] == "2026-10-30 20:00"


def test_past_after_midnight():
    now = datetime(2026, 10, 31, 1, 0, tzinfo=TZ)
    assert start_times(room("18:00", "02:00"), NIGHT, now) == [
        "2026-10-31 01:00", "2026-10-31 01:30", "2026-10-31 02:00",
    ]


def test_whole_night_past():
    now = datetime(2026, 10, 31, 3, 0, tzinfo=TZ)
    assert start_times(room("18:00", "02:00"), NIGHT, now) == []


def test_now_in_other_timezone():
    utc_now = datetime(2026, 10, 30, 12, 10, tzinfo=ZoneInfo("UTC"))   # = 台北 20:10
    assert start_times(room("18:00", "02:00"), NIGHT, utc_now)[0] == "2026-10-30 20:30"


# slot_label
def test_slot_label():
    assert slot_label("2026-10-30 20:00", 2) == "20:00–22:00"


def test_slot_label_cross_midnight():
    assert slot_label("2026-10-30 23:00", 2) == "23:00–01:00"
    assert slot_label("2026-10-31 01:30", 3) == "01:30–04:30"


# times_of_day
def test_times_of_day_order_cross_midnight():
    assert times_of_day(room("23:00", "01:00"), [NIGHT, "2026-10-31"], BEFORE) == [
        "23:00", "23:30", "00:00", "00:30", "01:00",
    ]


def test_times_of_day_tonight_partly_past():
    now = datetime(2026, 10, 30, 21, 10, tzinfo=TZ)
    r = room("20:00", "22:00")
    assert times_of_day(r, [NIGHT], now) == ["21:30", "22:00"]
    assert times_of_day(r, [NIGHT, "2026-10-31"], now) == ["20:00", "20:30", "21:00", "21:30", "22:00"]  # 明晚都還能選


def test_times_of_day_empty():
    assert times_of_day(room("20:00", "22:00"), [], BEFORE) == []
