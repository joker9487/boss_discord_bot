from datetime import datetime

import pytest

import schedule
from reminders import due_reminders
from rooms import TZ, cleanup, create_room, get_room, get_room_by_id, join_room
from schedule import decide, delay, reschedule, set_availability
from slots import slot_label

LEADER = 111
A = 222
NIGHT = "2026-10-30"
START = "2026-10-30 20:00"


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


NOW = at(30, 10)          # 10/30 早上 10 點


@pytest.fixture
def room(conn):
    key = create_room(conn, "週五打王", LEADER, 1, 2, "18:00", "02:00")
    room = join_room(conn, key, A)
    set_availability(conn, room["id"], A, NIGHT, [START, "2026-10-30 21:00"], NOW)
    decide(conn, room["id"], LEADER, START, NOW)
    return get_room(conn, key)


def reminders_of(conn, room_id):
    rows = conn.execute(
        "SELECT kind, remind_at, sent FROM reminders WHERE room_id=? ORDER BY kind, remind_at, id", (room_id,)
    )
    return [(r["kind"], r["remind_at"], r["sent"]) for r in rows]


def decided_at(conn, room):
    return get_room_by_id(conn, room["id"])["decided_at"]


# delay
def test_delay_moves_start_and_before(conn, room):
    assert delay(conn, room["id"], LEADER, 15, NOW) == "2026-10-30 20:15"
    assert decided_at(conn, room) == "2026-10-30 20:15"
    assert reminders_of(conn, room["id"]) == [
        ("before", "2026-10-30 20:05", 0),
        ("day", "2026-10-30 12:00", 0),          # day 不動
    ]


def test_delay_twice_adds_up(conn, room):
    delay(conn, room["id"], LEADER, 30, NOW)
    delay(conn, room["id"], LEADER, 60, NOW)
    assert decided_at(conn, room) == "2026-10-30 21:30"
    assert [r for r in reminders_of(conn, room["id"]) if r[0] == "before"] == [("before", "2026-10-30 21:20", 0)]


def test_delay_across_midnight(conn, room):
    conn.execute("UPDATE rooms SET decided_at='2026-10-30 23:30' WHERE id=?", (room["id"],))
    assert delay(conn, room["id"], LEADER, 60, NOW) == "2026-10-31 00:30"


def test_delay_after_before_sent_writes_new(conn, room):
    conn.execute("UPDATE reminders SET sent=1 WHERE kind='before'")      # 19:50 那則已經發了
    delay(conn, room["id"], LEADER, 30, at(30, 19, 55))
    assert [r for r in reminders_of(conn, room["id"]) if r[0] == "before"] == [
        ("before", "2026-10-30 19:50", 1),       # 發過的留著
        ("before", "2026-10-30 20:20", 0),
    ]


def test_delay_new_before_already_past_not_written(conn, room):
    delay(conn, room["id"], LEADER, 15, at(30, 20, 10))   # 新開打 20:15，提醒 20:05 已經過了
    assert [r for r in reminders_of(conn, room["id"]) if r[0] == "before"] == []


def test_delay_non_leader(conn, room):
    with pytest.raises(ValueError):
        delay(conn, room["id"], A, 15, NOW)
    assert decided_at(conn, room) == START


def test_delay_open_room(conn, room):
    reschedule(conn, room["id"], LEADER)
    with pytest.raises(ValueError):
        delay(conn, room["id"], LEADER, 15, NOW)


@pytest.mark.parametrize("bad", [0, -15, 1.5, "15", None])
def test_delay_bad_minutes(conn, room, bad):
    with pytest.raises(ValueError):
        delay(conn, room["id"], LEADER, bad, NOW)


def test_delay_room_not_found(conn):
    with pytest.raises(ValueError):
        delay(conn, 999, LEADER, 15, NOW)


def test_delay_rollback(conn, room, monkeypatch):
    def boom(*args):
        raise RuntimeError("boom")
    monkeypatch.setattr(schedule, "reset_before", boom)
    with pytest.raises(RuntimeError):
        delay(conn, room["id"], LEADER, 15, NOW)
    assert decided_at(conn, room) == START
    assert ("before", "2026-10-30 19:50", 0) in reminders_of(conn, room["id"])


# 延後後不在 30 分格子上
def test_off_grid_display():
    assert slot_label("2026-10-30 20:15", 2) == "20:15–22:15"


def test_off_grid_due_reminder_uses_new_start(conn, room):
    delay(conn, room["id"], LEADER, 15, NOW)
    due = due_reminders(conn, at(30, 20, 5))
    assert [(r["kind"], r["start_at"]) for r in due] == [("day", "2026-10-30 20:15"), ("before", "2026-10-30 20:15")]


def test_off_grid_cleanup(conn, room):
    delay(conn, room["id"], LEADER, 15, NOW)
    assert cleanup(conn, at(31, 20, 15)) == 0              # 剛好 24 小時還不刪
    assert cleanup(conn, at(31, 20, 16)) == 1


# reschedule
def test_reschedule(conn, room):
    reschedule(conn, room["id"], LEADER)
    r = get_room_by_id(conn, room["id"])
    assert (r["status"], r["decided_at"]) == ("open", None)
    assert reminders_of(conn, room["id"]) == []
    rows = conn.execute("SELECT start_at FROM availability WHERE room_id=? ORDER BY start_at", (room["id"],))
    assert [x["start_at"] for x in rows] == [START, "2026-10-30 21:00"]     # 填過的保留


def test_reschedule_then_decide_again(conn, room):
    reschedule(conn, room["id"], LEADER)
    decide(conn, room["id"], LEADER, "2026-10-30 21:00", NOW)
    assert decided_at(conn, room) == "2026-10-30 21:00"


def test_reschedule_non_leader(conn, room):
    with pytest.raises(ValueError):
        reschedule(conn, room["id"], A)
    assert get_room_by_id(conn, room["id"])["status"] == "decided"


def test_reschedule_open_room(conn, room):
    reschedule(conn, room["id"], LEADER)
    with pytest.raises(ValueError):
        reschedule(conn, room["id"], LEADER)


def test_reschedule_rollback(conn, room, monkeypatch):
    def boom(*args):
        raise RuntimeError("boom")
    monkeypatch.setattr(schedule, "clear_reminders", boom)
    with pytest.raises(RuntimeError):
        reschedule(conn, room["id"], LEADER)
    r = get_room_by_id(conn, room["id"])
    assert (r["status"], r["decided_at"]) == ("decided", START)
