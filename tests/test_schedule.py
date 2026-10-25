from datetime import datetime

import pytest

from rooms import TZ, create_room, get_room, join_room
from schedule import decide, ranking, set_availability

LEADER = 111
A = 222
B = 333
OUTSIDER = 999
NIGHT = "2026-10-30"
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=TZ)


@pytest.fixture
def room(conn):
    key = create_room(conn, "週五打王", LEADER, 1, 2, "18:00", "02:00")
    join_room(conn, key, A)
    return join_room(conn, key, B)


def picks(conn, room_id, user_id):
    rows = conn.execute(
        "SELECT start_at FROM availability WHERE room_id=? AND user_id=? ORDER BY start_at",
        (room_id, user_id),
    )
    return [r["start_at"] for r in rows]


def fill(conn, room, user_id, *starts, night=NIGHT):
    set_availability(conn, room["id"], user_id, night, list(starts), NOW)


# set_availability
def test_set_availability(conn, room):
    fill(conn, room, A, "2026-10-30 20:00", "2026-10-31 00:30")
    assert picks(conn, room["id"], A) == ["2026-10-30 20:00", "2026-10-31 00:30"]


def test_duplicate_starts_once(conn, room):
    fill(conn, room, A, "2026-10-30 20:00", "2026-10-30 20:00")
    assert picks(conn, room["id"], A) == ["2026-10-30 20:00"]


def test_refill_overwrites(conn, room):
    fill(conn, room, A, "2026-10-30 18:00", "2026-10-31 01:00")
    fill(conn, room, A, "2026-10-30 21:00")
    assert picks(conn, room["id"], A) == ["2026-10-30 21:00"]


def test_empty_clears_only_that_night(conn, room):
    fill(conn, room, A, "2026-10-30 20:00", "2026-10-31 01:00")
    fill(conn, room, A, "2026-10-31 20:00", night="2026-10-31")
    fill(conn, room, A)                                            # 清掉 10/30 那一晚（含跨午夜的 01:00）
    assert picks(conn, room["id"], A) == ["2026-10-31 20:00"]


def test_refill_does_not_touch_others(conn, room):
    fill(conn, room, A, "2026-10-30 20:00")
    fill(conn, room, B, "2026-10-30 20:00")
    fill(conn, room, A)
    assert picks(conn, room["id"], B) == ["2026-10-30 20:00"]


def test_non_member_cannot_fill(conn, room):
    with pytest.raises(ValueError):
        fill(conn, room, OUTSIDER, "2026-10-30 20:00")
    assert picks(conn, room["id"], OUTSIDER) == []


def test_decided_cannot_fill(conn, room):
    fill(conn, room, A, "2026-10-30 20:00")
    decide(conn, room["id"], LEADER, "2026-10-30 20:00")
    with pytest.raises(ValueError):
        fill(conn, room, A, "2026-10-30 21:00")
    assert picks(conn, room["id"], A) == ["2026-10-30 20:00"]


@pytest.mark.parametrize("bad", [
    "2026-10-30 17:30",   # 範圍外
    "2026-10-31 02:30",   # 範圍外（跨午夜之後）
    "2026-10-30 20:15",   # 不是整點 / 半點
    "2026-10-31 20:00",   # 別晚的時間
])
def test_start_not_in_options(conn, room, bad):
    fill(conn, room, A, "2026-10-30 20:00")
    with pytest.raises(ValueError):
        fill(conn, room, A, "2026-10-30 21:00", bad)
    assert picks(conn, room["id"], A) == ["2026-10-30 20:00"]       # 失敗不會刪掉舊的


def test_past_start_rejected(conn, room):
    now = datetime(2026, 10, 30, 20, 10, tzinfo=TZ)
    with pytest.raises(ValueError):
        set_availability(conn, room["id"], A, NIGHT, ["2026-10-30 20:00"], now)
    set_availability(conn, room["id"], A, NIGHT, ["2026-10-30 20:30"], now)
    assert picks(conn, room["id"], A) == ["2026-10-30 20:30"]


def test_room_not_found(conn):
    with pytest.raises(ValueError):
        set_availability(conn, 12345, A, NIGHT, [], NOW)


# ranking
def test_ranking_order(conn, room):
    fill(conn, room, LEADER, "2026-10-30 18:00", "2026-10-30 20:00", "2026-10-31 00:00")
    fill(conn, room, A, "2026-10-30 20:00", "2026-10-30 21:00", "2026-10-31 00:00")
    fill(conn, room, B, "2026-10-30 20:00", "2026-10-30 21:00")
    result = ranking(conn, room["id"])
    assert result["members"] == 3
    assert result["slots"] == [
        {"start_at": "2026-10-30 20:00", "count": 3, "user_ids": [LEADER, A, B]},   # 全員
        {"start_at": "2026-10-30 21:00", "count": 2, "user_ids": [A, B]},           # 同人數 → 時間早的先
        {"start_at": "2026-10-31 00:00", "count": 2, "user_ids": [LEADER, A]},
        {"start_at": "2026-10-30 18:00", "count": 1, "user_ids": [LEADER]},
    ]


def test_ranking_top(conn, room):
    fill(conn, room, A, "2026-10-30 18:00", "2026-10-30 19:00", "2026-10-30 20:00")
    starts = [s["start_at"] for s in ranking(conn, room["id"], top=2)["slots"]]
    assert starts == ["2026-10-30 18:00", "2026-10-30 19:00"]


def test_ranking_empty(conn, room):
    assert ranking(conn, room["id"]) == {"members": 3, "slots": []}


# decide
def test_decide(conn, room):
    decide(conn, room["id"], LEADER, "2026-10-30 20:00")
    r = get_room(conn, room["key"])
    assert r["status"] == "decided"
    assert r["decided_at"] == "2026-10-30 20:00"


def test_non_leader_cannot_decide(conn, room):
    with pytest.raises(ValueError):
        decide(conn, room["id"], A, "2026-10-30 20:00")
    r = get_room(conn, room["key"])
    assert r["status"] == "open"
    assert r["decided_at"] is None


def test_decide_twice(conn, room):
    decide(conn, room["id"], LEADER, "2026-10-30 20:00")
    with pytest.raises(ValueError):
        decide(conn, room["id"], LEADER, "2026-10-30 21:00")
    assert get_room(conn, room["key"])["decided_at"] == "2026-10-30 20:00"
