from datetime import datetime

import pytest

from reminders import due_reminders, mark_sent
from rooms import TZ, create_room, delete_room, get_room
from schedule import decide


def at(month, day, hour, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=TZ)


NOW = at(10, 30, 10)         # 10/30 早上 10 點定案
LEADER = 111


def make_room(conn, start="18:00", end="02:00"):
    return get_room(conn, create_room(conn, "週五打王", LEADER, 1, 2, start, end))


def reminders_of(conn, room_id):
    rows = conn.execute(
        "SELECT kind, remind_at, sent FROM reminders WHERE room_id=? ORDER BY remind_at", (room_id,)
    )
    return [(r["kind"], r["remind_at"], r["sent"]) for r in rows]


# 定案寫入提醒
def test_decide_writes_two(conn):
    room = make_room(conn)
    decide(conn, room["id"], LEADER, "2026-10-30 20:00", NOW)
    assert reminders_of(conn, room["id"]) == [
        ("day", "2026-10-30 12:00", 0),
        ("before", "2026-10-30 19:50", 0),
    ]


def test_after_midnight_day_is_night_before(conn):
    room = make_room(conn)
    decide(conn, room["id"], LEADER, "2026-10-31 01:30", NOW)          # 10/30 那一晚
    assert reminders_of(conn, room["id"]) == [
        ("day", "2026-10-30 12:00", 0),
        ("before", "2026-10-31 01:20", 0),
    ]


def test_past_day_not_written(conn):
    room = make_room(conn)
    decide(conn, room["id"], LEADER, "2026-10-30 20:00", at(10, 30, 15))
    assert reminders_of(conn, room["id"]) == [("before", "2026-10-30 19:50", 0)]


def test_both_past_not_written(conn):
    room = make_room(conn)
    decide(conn, room["id"], LEADER, "2026-10-30 20:00", at(10, 30, 19, 55))
    assert reminders_of(conn, room["id"]) == []


def test_now_exactly_reminder_time_written(conn):
    room = make_room(conn)
    decide(conn, room["id"], LEADER, "2026-10-30 20:00", at(10, 30, 19, 50))
    assert reminders_of(conn, room["id"]) == [("before", "2026-10-30 19:50", 0)]


def test_day_after_start_not_written(conn):
    room = make_room(conn, "00:00", "06:00")                           # 不跨午夜的凌晨場
    decide(conn, room["id"], LEADER, "2026-10-31 01:00", NOW)          # 那一晚是 10/31，12:00 比開打還晚
    assert reminders_of(conn, room["id"]) == [("before", "2026-10-31 00:50", 0)]


def test_decide_failed_no_reminders(conn):
    room = make_room(conn)
    with pytest.raises(ValueError):
        decide(conn, room["id"], LEADER, "2026-10-30 17:00", NOW)
    assert reminders_of(conn, room["id"]) == []


def test_decide_rolls_back_if_reminders_fail(conn, monkeypatch):
    import schedule

    def boom(*args):
        raise RuntimeError("寫提醒失敗")
    monkeypatch.setattr(schedule, "add_reminders", boom)
    room = make_room(conn)
    with pytest.raises(RuntimeError):
        decide(conn, room["id"], LEADER, "2026-10-30 20:00", NOW)
    r = get_room(conn, room["key"])
    assert (r["status"], r["decided_at"]) == ("open", None)            # 同一次 commit：一起失敗


def test_delete_room_cascades_reminders(conn):
    room = make_room(conn)
    decide(conn, room["id"], LEADER, "2026-10-30 20:00", NOW)
    delete_room(conn, room["id"])
    assert reminders_of(conn, room["id"]) == []


# due_reminders / mark_sent
def test_due_reminders_flow(conn):
    room = make_room(conn)
    decide(conn, room["id"], LEADER, "2026-10-30 20:00", NOW)

    assert due_reminders(conn, at(10, 30, 11, 59)) == []

    due = due_reminders(conn, at(10, 30, 12))
    assert [(r["kind"], r["start_at"]) for r in due] == [("day", "2026-10-30 20:00")]
    mark_sent(conn, due[0]["id"])
    assert due_reminders(conn, at(10, 30, 12, 1)) == []

    due = due_reminders(conn, at(10, 30, 19, 50))
    assert [r["kind"] for r in due] == ["before"]


def test_late_but_before_start_still_sent(conn):
    room = make_room(conn)
    decide(conn, room["id"], LEADER, "2026-10-30 20:00", NOW)
    due = due_reminders(conn, at(10, 30, 19, 55))                      # bot 晚開機，還沒開打 → 兩筆都照發
    assert [r["kind"] for r in due] == ["day", "before"]


def test_missed_marked_sent_not_returned(conn):
    room = make_room(conn)
    decide(conn, room["id"], LEADER, "2026-10-30 20:00", NOW)
    assert due_reminders(conn, at(10, 30, 20, 30)) == []               # 已經開打 → 不發
    assert [r[2] for r in reminders_of(conn, room["id"])] == [1, 1]    # 但標成 sent


def test_due_multiple_rooms(conn):
    r1 = make_room(conn)
    r2 = make_room(conn)
    decide(conn, r1["id"], LEADER, "2026-10-30 20:00", NOW)
    decide(conn, r2["id"], LEADER, "2026-10-30 21:00", NOW)
    due = due_reminders(conn, at(10, 30, 12))
    assert sorted(r["room_id"] for r in due) == [r1["id"], r2["id"]]
