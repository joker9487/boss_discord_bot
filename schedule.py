from datetime import datetime, timedelta

from reminders import add_reminders, clear_reminders, reset_before
from rooms import TIME_FMT, get_room_by_id, is_leader, now_str
from slots import all_start_times, night_of, start_times, times_of_day


#用 id 找 room；找不到 → ValueError
def _get_room(conn, room_id):
    room = get_room_by_id(conn, room_id)
    if room is None:
        raise ValueError("房間不存在")
    return room


def _is_member(conn, room_id, user_id):
    row = conn.execute("SELECT 1 FROM members WHERE room_id=? AND user_id=?", (room_id, user_id)).fetchone()
    return row is not None


#start_at 是不是這個房間合法、還沒過去的開始時間
def _is_valid_start(room, start_at, now):
    try:
        night = night_of(room, start_at)
    except (TypeError, ValueError):
        return False
    return night is not None and start_at in start_times(room, night, now)


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


#那一晚這個人已經勾的時間（選單預先勾選用）
def get_availability(conn, room_id, user_id, night):
    night_slots = all_start_times(_get_room(conn, room_id), night)
    rows = conn.execute(
        "SELECT start_at FROM availability WHERE room_id=? AND user_id=? AND start_at BETWEEN ? AND ? "
        "ORDER BY start_at",
        (room_id, user_id, night_slots[0], night_slots[-1]),
    )
    return [r["start_at"] for r in rows]


#每個還沒過去的開始時間有幾人、是誰；人數多 → 時間早；members 是成員總數（count == members 就是全員）
def ranking(conn, room_id, top=5, now=None):
    members = conn.execute("SELECT COUNT(*) FROM members WHERE room_id=?", (room_id,)).fetchone()[0]
    rows = conn.execute(
        "SELECT start_at, COUNT(*) AS cnt FROM availability WHERE room_id=? AND start_at >= ? "
        "GROUP BY start_at ORDER BY cnt DESC, start_at LIMIT ?",
        (room_id, now_str(now), top),
    ).fetchall()
    slots = []
    for r in rows:
        users = conn.execute(
            "SELECT user_id FROM availability WHERE room_id=? AND start_at=? ORDER BY user_id",
            (room_id, r["start_at"]),
        )
        slots.append({"start_at": r["start_at"], "count": r["cnt"], "user_ids": [u["user_id"] for u in users]})
    return {"members": members, "slots": slots}


#定案：只有隊長、只有 open、start_at 要是合法且還沒過去的時段；狀態和提醒同一次 commit
def decide(conn, room_id, user_id, start_at, now=None):
    room = _get_room(conn, room_id)
    if not is_leader(room, user_id):
        raise ValueError("只有隊長能定案")
    if room["status"] != "open":
        raise ValueError("房間已經定案")
    if not _is_valid_start(room, start_at, now):
        raise ValueError(f"不能定案的時間：{start_at!r}")
    try:
        conn.execute("UPDATE rooms SET status='decided', decided_at=? WHERE id=?", (start_at, room_id))
        add_reminders(conn, room, start_at, now)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


#已定案的房間給隊長操作（延後、改期）；不是隊長、還沒定案 → ValueError
def _decided_room_of_leader(conn, room_id, leader_id):
    room = _get_room(conn, room_id)
    if not is_leader(room, leader_id):
        raise ValueError("只有隊長能操作")
    if room["status"] != "decided":
        raise ValueError("房間還沒定案")
    return room


#延後：decided_at += minutes；before 提醒用新時間重寫，day 不動；回傳新的開打時間
def delay(conn, room_id, leader_id, minutes, now=None):
    room = _decided_room_of_leader(conn, room_id, leader_id)
    if not isinstance(minutes, int) or minutes <= 0:
        raise ValueError("延後的分鐘數要是正整數")
    new_start = (datetime.strptime(room["decided_at"], TIME_FMT) + timedelta(minutes=minutes)).strftime(TIME_FMT)
    try:
        conn.execute("UPDATE rooms SET decided_at=? WHERE id=?", (new_start, room_id))
        reset_before(conn, room_id, new_start, now)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return new_start


#改期：狀態改回 open、清掉定案時間和所有提醒；大家填過的時間保留
def reschedule(conn, room_id, leader_id):
    _decided_room_of_leader(conn, room_id, leader_id)
    try:
        conn.execute("UPDATE rooms SET status='open', decided_at=NULL WHERE id=?", (room_id,))
        clear_reminders(conn, room_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


#多晚一起填：hhmms（"HH:MM"）套到每一晚，某一晚已經過去的時間跳過；hhmms 空 = 這幾晚都清掉；回傳實際寫入幾個時段
def set_availability_nights(conn, room_id, user_id, nights, hhmms, now=None):
    room = _get_room(conn, room_id)
    nights = list(dict.fromkeys(nights))
    if not nights:
        raise ValueError("至少要選一晚")
    hhmms = set(hhmms)
    bad = hhmms - set(times_of_day(room, nights, now))
    if bad:
        raise ValueError(f"不能選的時間：{sorted(bad)}")
    total = 0
    for night in nights:
        starts = [s for s in start_times(room, night, now) if s[-5:] in hhmms]
        set_availability(conn, room_id, user_id, night, starts, now)
        total += len(starts)
    return total


#多晚一起填的預先勾選：每一晚都有勾的 "HH:MM"
def common_availability(conn, room_id, user_id, nights):
    sets = [{s[-5:] for s in get_availability(conn, room_id, user_id, n)} for n in nights]
    return set.intersection(*sets) if sets else set()


#還沒填（沒有任何還沒過去的時間）的成員，照加入順序
def unfilled_members(conn, room_id, now=None):
    rows = conn.execute(
        "SELECT user_id FROM members WHERE room_id=? AND user_id NOT IN "
        "(SELECT user_id FROM availability WHERE room_id=? AND start_at >= ?) ORDER BY rowid",
        (room_id, room_id, now_str(now)),
    )
    return [r["user_id"] for r in rows]
