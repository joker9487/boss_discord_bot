from datetime import datetime

import pytest

from rooms import TZ, create_room, join_room
from schedule import (
    common_availability, decide, get_availability, set_availability, set_availability_nights, unfilled_members,
)

LEADER = 111
A = 222
B = 333
OUTSIDER = 999
FRI, SAT, SUN = "2026-10-30", "2026-10-31", "2026-11-01"
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=TZ)


@pytest.fixture
def room(conn):
    key = create_room(conn, "週五打王", LEADER, 1, 2, "20:00", "01:00")
    join_room(conn, key, A)
    return join_room(conn, key, B)


def fill_nights(conn, room, user_id, nights, hhmms, now=NOW):
    return set_availability_nights(conn, room["id"], user_id, nights, hhmms, now)


# set_availability_nights
def test_same_times_every_night(conn, room):
    assert fill_nights(conn, room, A, [FRI, SAT], ["21:00", "00:30"]) == 4
    assert get_availability(conn, room["id"], A, FRI) == ["2026-10-30 21:00", "2026-10-31 00:30"]
    assert get_availability(conn, room["id"], A, SAT) == ["2026-10-31 21:00", "2026-11-01 00:30"]


def test_skips_past_on_tonight(conn, room):
    now = datetime(2026, 10, 30, 21, 10, tzinfo=TZ)
    assert fill_nights(conn, room, A, [FRI, SAT], ["21:00", "22:00"], now) == 3
    assert get_availability(conn, room["id"], A, FRI) == ["2026-10-30 22:00"]          # 今晚 21:00 已過，跳過
    assert get_availability(conn, room["id"], A, SAT) == ["2026-10-31 21:00", "2026-10-31 22:00"]


def test_overwrites_only_chosen_nights(conn, room):
    fill_nights(conn, room, A, [FRI, SAT, SUN], ["21:00"])
    fill_nights(conn, room, A, [FRI, SAT], ["23:00"])
    assert get_availability(conn, room["id"], A, FRI) == ["2026-10-30 23:00"]
    assert get_availability(conn, room["id"], A, SUN) == ["2026-11-01 21:00"]


def test_empty_clears_chosen_nights(conn, room):
    fill_nights(conn, room, A, [FRI, SAT], ["21:00"])
    assert fill_nights(conn, room, A, [FRI, SAT], []) == 0
    assert get_availability(conn, room["id"], A, FRI) == []
    assert get_availability(conn, room["id"], A, SAT) == []


def test_duplicate_nights_once(conn, room):
    assert fill_nights(conn, room, A, [FRI, FRI], ["21:00"]) == 1


@pytest.mark.parametrize("bad", [["19:00"], ["21:15"], ["abc"]])
def test_bad_time(conn, room, bad):
    with pytest.raises(ValueError):
        fill_nights(conn, room, A, [FRI], bad)


def test_no_nights(conn, room):
    with pytest.raises(ValueError):
        fill_nights(conn, room, A, [], ["21:00"])


def test_non_member(conn, room):
    with pytest.raises(ValueError):
        fill_nights(conn, room, OUTSIDER, [FRI, SAT], ["21:00"])


def test_decided_room(conn, room):
    fill_nights(conn, room, A, [FRI], ["21:00"])
    decide(conn, room["id"], LEADER, "2026-10-30 21:00", NOW)
    with pytest.raises(ValueError):
        fill_nights(conn, room, A, [SAT], ["21:00"])


# common_availability
def test_common_availability(conn, room):
    fill_nights(conn, room, A, [FRI, SAT], ["21:00", "00:30"])
    set_availability(conn, room["id"], A, SAT, ["2026-10-31 21:00"], NOW)
    assert common_availability(conn, room["id"], A, [FRI, SAT]) == {"21:00"}
    assert common_availability(conn, room["id"], A, [FRI]) == {"21:00", "00:30"}
    assert common_availability(conn, room["id"], A, [FRI, SUN]) == set()
    assert common_availability(conn, room["id"], A, []) == set()


# unfilled_members
def test_unfilled_members(conn, room):
    assert unfilled_members(conn, room["id"], NOW) == [LEADER, A, B]
    fill_nights(conn, room, A, [FRI], ["21:00"])
    assert unfilled_members(conn, room["id"], NOW) == [LEADER, B]


def test_unfilled_counts_only_future(conn, room):
    fill_nights(conn, room, A, [FRI], ["21:00"])
    later = datetime(2026, 10, 30, 21, 30, tzinfo=TZ)         # A 只填了已經過去的 21:00
    assert unfilled_members(conn, room["id"], later) == [LEADER, A, B]


def test_unfilled_other_room_not_counted(conn, room):
    other = join_room(conn, create_room(conn, "別房", A, 1, 2, "20:00", "01:00"), A)
    set_availability(conn, other["id"], A, FRI, ["2026-10-30 21:00"], NOW)
    assert A in unfilled_members(conn, room["id"], NOW)
