import sqlite3

import pytest

import rooms
from datetime import datetime

from rooms import (
    KEY_CHARS, KEY_RETRY, TZ, cleanup, create_room, delete_room, digits_to_hhmm, get_room, get_room_by_id, is_leader, join_room,
    kick, leave_room, member_ids, user_rooms,
)

LEADER = 111
OTHER = 222
CHANNEL = 999


def make_room(conn, duration=2):
    return create_room(conn, "週五打王", LEADER, CHANNEL, duration, "18:00", "02:00")


# 建房
def test_create_room_key_6_chars(conn):
    key = make_room(conn)
    assert len(key) == 6
    assert all(c in KEY_CHARS for c in key)


def test_create_room_saved(conn):
    key = make_room(conn)
    room = get_room(conn, key)
    assert room["name"] == "週五打王"
    assert room["leader_id"] == LEADER
    assert room["status"] == "open"


def test_create_room_leader_is_member(conn):
    room = get_room(conn, make_room(conn))
    assert member_ids(conn, room["id"]) == [LEADER]


# key 撞到重抽
def test_key_collision_retries(conn, monkeypatch):
    keys = iter(["AAAAAA", "AAAAAA", "BBBBBB"])
    monkeypatch.setattr(rooms, "generate_key", lambda: next(keys))
    assert make_room(conn) == "AAAAAA"
    assert make_room(conn) == "BBBBBB"   # 第二次先撞到 AAAAAA，重抽成 BBBBBB


def test_key_collision_gives_up(conn, monkeypatch):
    calls = []
    def same_key():
        calls.append(1)
        return "AAAAAA"
    monkeypatch.setattr(rooms, "generate_key", same_key)
    make_room(conn)
    calls.clear()
    with pytest.raises(sqlite3.IntegrityError):
        make_room(conn)
    assert len(calls) == KEY_RETRY


def test_other_integrity_error_not_retried(conn, monkeypatch):
    calls = []
    def counting_key():
        calls.append(1)
        return f"K{len(calls):05d}"
    monkeypatch.setattr(rooms, "generate_key", counting_key)
    with pytest.raises(sqlite3.IntegrityError):
        create_room(conn, None, LEADER, CHANNEL, 2, "18:00", "02:00")   # name 是 NOT NULL
    assert len(calls) == 1


# 時長
@pytest.mark.parametrize("duration", [1, 2, 3])
def test_duration_ok(conn, duration):
    assert get_room(conn, make_room(conn, duration))["duration"] == duration


@pytest.mark.parametrize("duration", [0, 4, -1])
def test_duration_out_of_range(conn, duration):
    with pytest.raises(ValueError):
        make_room(conn, duration)
    assert conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0


# 開始時間範圍
@pytest.mark.parametrize("start, end", [
    ("18:00", "02:00"),   # 跨午夜
    ("23:00", "01:00"),
    ("18:00", "06:00"),   # 剛好 12 小時（跨午夜）
    ("06:00", "18:00"),   # 剛好 12 小時（同一天）
    ("20:30", "21:00"),
])
def test_range_ok(conn, start, end):
    key = create_room(conn, "房", LEADER, CHANNEL, 2, start, end)
    assert get_room(conn, key)["range_start"] == start


@pytest.mark.parametrize("start, end", [
    ("18:00", "06:30"),   # 12.5 小時（跨午夜）
    ("06:00", "18:30"),   # 12.5 小時（同一天）
    ("18:00", "18:00"),   # end == start → 跨午夜 24 小時
])
def test_range_too_long(conn, start, end):
    with pytest.raises(ValueError):
        create_room(conn, "房", LEADER, CHANNEL, 2, start, end)


@pytest.mark.parametrize("bad", ["18:15", "8:00", "24:00", "18-00", "aa:bb", "18:00 ", "", None])
def test_range_bad_format(conn, bad):
    with pytest.raises(ValueError):
        create_room(conn, "房", LEADER, CHANNEL, 2, bad, "22:00")
    with pytest.raises(ValueError):
        create_room(conn, "房", LEADER, CHANNEL, 2, "18:00", bad)
    assert conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0


# 加入
def test_join_room(conn):
    key = make_room(conn)
    room = join_room(conn, key, OTHER)
    assert room["key"] == key
    assert sorted(member_ids(conn, room["id"])) == [LEADER, OTHER]


def test_join_unknown_key(conn):
    make_room(conn)
    assert join_room(conn, "NOPE00", OTHER) is None


def test_join_twice_counts_once(conn):
    key = make_room(conn)
    join_room(conn, key, OTHER)
    room = join_room(conn, key, OTHER)
    assert member_ids(conn, room["id"]).count(OTHER) == 1


# 成員 / 我的房間
def test_member_ids_join_order(conn):
    key = make_room(conn)
    join_room(conn, key, 50)
    room = join_room(conn, key, 30)
    assert member_ids(conn, room["id"]) == [LEADER, 50, 30]


def test_user_rooms(conn):
    k1 = make_room(conn)
    k2 = make_room(conn)
    k3 = make_room(conn)
    join_room(conn, k1, OTHER)
    join_room(conn, k3, OTHER)
    assert [r["key"] for r in user_rooms(conn, OTHER)] == [k1, k3]
    assert [r["key"] for r in user_rooms(conn, LEADER)] == [k1, k2, k3]
    assert user_rooms(conn, 12345) == []


def test_user_rooms_after_delete(conn):
    room = join_room(conn, make_room(conn), OTHER)
    delete_room(conn, room["id"])
    assert user_rooms(conn, OTHER) == []


# 權限
def test_is_leader(conn):
    room = get_room(conn, make_room(conn))
    assert is_leader(room, LEADER)
    assert not is_leader(room, OTHER)


def test_get_room_by_id(conn):
    key = make_room(conn)
    room = get_room(conn, key)
    assert get_room_by_id(conn, room["id"])["key"] == key
    assert get_room_by_id(conn, 12345) is None


# 退出 / 踢人
def add_pick(conn, room_id, user_id, start_at="2026-10-30 20:00"):
    conn.execute("INSERT INTO availability (room_id, user_id, start_at) VALUES (?, ?, ?)", (room_id, user_id, start_at))
    conn.commit()


def picks(conn, room_id, user_id):
    rows = conn.execute("SELECT start_at FROM availability WHERE room_id=? AND user_id=?", (room_id, user_id))
    return [r["start_at"] for r in rows]


def test_leave_room(conn):
    room = join_room(conn, make_room(conn), OTHER)
    other_room = join_room(conn, make_room(conn), OTHER)
    add_pick(conn, room["id"], OTHER)
    add_pick(conn, room["id"], LEADER)
    add_pick(conn, other_room["id"], OTHER)
    leave_room(conn, room["id"], OTHER)
    assert member_ids(conn, room["id"]) == [LEADER]
    assert picks(conn, room["id"], OTHER) == []                         # 他在這房間填的也刪掉
    assert picks(conn, room["id"], LEADER) == ["2026-10-30 20:00"]      # 別人的不動
    assert picks(conn, other_room["id"], OTHER) == ["2026-10-30 20:00"] # 別的房間不動


def test_leader_cannot_leave(conn):
    room = get_room(conn, make_room(conn))
    with pytest.raises(ValueError):
        leave_room(conn, room["id"], LEADER)
    assert member_ids(conn, room["id"]) == [LEADER]


def test_leave_not_member(conn):
    room = get_room(conn, make_room(conn))
    with pytest.raises(ValueError):
        leave_room(conn, room["id"], OTHER)


def test_leave_room_not_found(conn):
    with pytest.raises(ValueError):
        leave_room(conn, 12345, OTHER)


def test_kick(conn):
    room = join_room(conn, make_room(conn), OTHER)
    add_pick(conn, room["id"], OTHER)
    kick(conn, room["id"], LEADER, OTHER)
    assert member_ids(conn, room["id"]) == [LEADER]
    assert picks(conn, room["id"], OTHER) == []


def test_non_leader_cannot_kick(conn):
    key = make_room(conn)
    join_room(conn, key, OTHER)
    room = join_room(conn, key, 333)
    with pytest.raises(ValueError):
        kick(conn, room["id"], OTHER, 333)
    assert member_ids(conn, room["id"]) == [LEADER, OTHER, 333]


def test_cannot_kick_self(conn):
    room = get_room(conn, make_room(conn))
    with pytest.raises(ValueError):
        kick(conn, room["id"], LEADER, LEADER)


def test_kick_not_member(conn):
    room = get_room(conn, make_room(conn))
    with pytest.raises(ValueError):
        kick(conn, room["id"], LEADER, OTHER)


# 自動清理
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=TZ)


def room_with(conn, created_at, decided_at=None, duration=2):
    room = get_room(conn, make_room(conn, duration))
    status = "decided" if decided_at else "open"
    conn.execute(
        "UPDATE rooms SET created_at=?, status=?, decided_at=? WHERE id=?",
        (created_at, status, decided_at, room["id"]),
    )
    conn.commit()
    return room["id"]


def left_ids(conn):
    return {r["id"] for r in conn.execute("SELECT id FROM rooms")}


def test_cleanup(conn):
    ended = room_with(conn, "2026-10-28 10:00", "2026-10-30 09:00")         # 2 小時，11:00 打完
    playing = room_with(conn, "2026-10-20 10:00", "2026-10-30 10:30")       # 建很久，但 12:30 才打完
    old_open = room_with(conn, "2026-10-23 11:59")                         # 7 天又 1 分沒定案
    new_open = room_with(conn, "2026-10-23 12:30")                         # 6 天多
    future = room_with(conn, "2026-10-29 10:00", "2026-10-30 20:00")
    assert cleanup(conn, NOW) == 2
    assert left_ids(conn) == {playing, new_open, future}
    assert member_ids(conn, ended) == []                                   # CASCADE
    assert member_ids(conn, old_open) == []


def test_cleanup_exactly_at_end(conn):
    just_ended = room_with(conn, "2026-10-29 10:00", "2026-10-30 10:00")     # 10:00 + 2 小時 = 12:00 = now → 刪
    one_min_left = room_with(conn, "2026-10-29 10:00", "2026-10-30 10:01")   # 12:01 才打完
    assert cleanup(conn, NOW) == 1
    assert left_ids(conn) == {one_min_left}
    assert just_ended not in left_ids(conn)


@pytest.mark.parametrize("duration, decided_at, deleted", [
    (1, "2026-10-30 11:00", True),       # 12:00 打完
    (1, "2026-10-30 11:30", False),      # 12:30 打完
    (3, "2026-10-30 09:00", True),       # 12:00 打完
    (3, "2026-10-30 09:30", False),      # 12:30 打完
])
def test_cleanup_uses_duration(conn, duration, decided_at, deleted):
    room_with(conn, "2026-10-29 10:00", decided_at, duration)
    assert cleanup(conn, NOW) == (1 if deleted else 0)


def test_cleanup_cross_midnight(conn):
    room_with(conn, "2026-10-28 10:00", "2026-10-29 23:00", 3)               # 10/30 02:00 打完
    assert cleanup(conn, datetime(2026, 10, 30, 1, 59, tzinfo=TZ)) == 0
    assert cleanup(conn, datetime(2026, 10, 30, 2, 0, tzinfo=TZ)) == 1


def test_cleanup_nothing(conn):
    room_with(conn, "2026-10-30 10:00")
    assert cleanup(conn, NOW) == 0


# 刪房間
def test_delete_room_cascades_members(conn):
    key = make_room(conn)
    room = join_room(conn, key, OTHER)
    delete_room(conn, room["id"])
    assert get_room(conn, key) is None
    assert member_ids(conn, room["id"]) == []


# 建房輸入框 HHMM → HH:MM
@pytest.mark.parametrize("text, expected", [("1800", "18:00"), ("0230", "02:30"), (" 2100 ", "21:00")])
def test_digits_to_hhmm(text, expected):
    assert digits_to_hhmm(text) == expected


@pytest.mark.parametrize("bad", ["", "180", "18000", "18:00", "18 0", "abcd"])
def test_digits_to_hhmm_bad(bad):
    with pytest.raises(ValueError):
        digits_to_hhmm(bad)
