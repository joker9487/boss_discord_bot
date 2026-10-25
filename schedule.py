from rooms import is_leader
from slots import all_start_times, start_times


#用 id 找 room；找不到 → ValueError
def _get_room(conn, room_id):
    room = conn.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
    if room is None:
        raise ValueError("房間不存在")
    return room


def _is_member(conn, room_id, user_id):
    row = conn.execute("SELECT 1 FROM members WHERE room_id=? AND user_id=?", (room_id, user_id)).fetchone()
    return row is not None


#填那一晚可以開始的時間；先刪掉這個人那一晚的全部時段再寫入（starts 空 list = 清掉那一晚）
def set_availability(conn, room_id, user_id, night, starts, now=None):
    room = _get_room(conn, room_id)
    if not _is_member(conn, room_id, user_id):
        raise ValueError("不是這個房間的成員")
    if room["status"] != "open":
        raise ValueError("房間已經定案，不能再填")
    allowed = set(start_times(room, night, now))
    starts = sorted(set(starts))
    bad = [s for s in starts if s not in allowed]
    if bad:
        raise ValueError(f"不能選的時間：{bad}")

    night_slots = all_start_times(room, night)
    conn.execute(
        "DELETE FROM availability WHERE room_id=? AND user_id=? AND start_at BETWEEN ? AND ?",
        (room_id, user_id, night_slots[0], night_slots[-1]),
    )
    conn.executemany(
        "INSERT INTO availability (room_id, user_id, start_at) VALUES (?, ?, ?)",
        [(room_id, user_id, s) for s in starts],
    )
    conn.commit()


#每個開始時間有幾人、是誰；人數多 → 時間早；members 是成員總數（count == members 就是全員）
def ranking(conn, room_id, top=5):
    members = conn.execute("SELECT COUNT(*) FROM members WHERE room_id=?", (room_id,)).fetchone()[0]
    rows = conn.execute(
        "SELECT start_at, COUNT(*) AS cnt FROM availability WHERE room_id=? "
        "GROUP BY start_at ORDER BY cnt DESC, start_at LIMIT ?",
        (room_id, top),
    ).fetchall()
    slots = []
    for r in rows:
        users = conn.execute(
            "SELECT user_id FROM availability WHERE room_id=? AND start_at=? ORDER BY user_id",
            (room_id, r["start_at"]),
        )
        slots.append({"start_at": r["start_at"], "count": r["cnt"], "user_ids": [u["user_id"] for u in users]})
    return {"members": members, "slots": slots}


#定案：只有隊長、只有 open 可以
def decide(conn, room_id, user_id, start_at):
    room = _get_room(conn, room_id)
    if not is_leader(room, user_id):
        raise ValueError("只有隊長能定案")
    if room["status"] != "open":
        raise ValueError("房間已經定案")
    conn.execute("UPDATE rooms SET status='decided', decided_at=? WHERE id=?", (start_at, room_id))
    conn.commit()
