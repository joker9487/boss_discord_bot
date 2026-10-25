import sqlite3

import pytest

import rooms
from rooms import (
    KEY_CHARS, KEY_RETRY, create_room, delete_room, get_room, is_leader, join_room, member_ids, user_rooms,
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


# 刪房間
def test_delete_room_cascades_members(conn):
    key = make_room(conn)
    room = join_room(conn, key, OTHER)
    delete_room(conn, room["id"])
    assert get_room(conn, key) is None
    assert member_ids(conn, room["id"]) == []
