from datetime import datetime, timedelta

from rooms import TIME_FMT, now_str
from slots import night_of

DAY_AT = "12:00"          # 當天提醒：那一晚的 12:00
BEFORE = 10               # 開始前幾分鐘提醒


#定案 → 寫入提醒（day / before）；已經過去的不寫。不 commit，跟 decide 一起 commit
def add_reminders(conn, room, start_at, now=None):
    now_s = now_str(now)
    start = datetime.strptime(start_at, TIME_FMT)
    candidates = [
        ("day", f"{night_of(room, start_at)} {DAY_AT}"),
        ("before", (start - timedelta(minutes=BEFORE)).strftime(TIME_FMT)),
    ]
    rows = [
        (room["id"], remind_at, kind)
        for kind, remind_at in candidates
        if now_s <= remind_at < start_at      # 過去的不寫；比開打還晚的（例如凌晨場的 12:00）也不寫
    ]
    conn.executemany("INSERT INTO reminders (room_id, remind_at, kind) VALUES (?, ?, ?)", rows)


#延後用：刪掉還沒發的 before，用新的開打時間重寫一筆（已經過去就不寫）；day 不動。不 commit
def reset_before(conn, room_id, start_at, now=None):
    conn.execute("DELETE FROM reminders WHERE room_id=? AND kind='before' AND sent=0", (room_id,))
    remind_at = (datetime.strptime(start_at, TIME_FMT) - timedelta(minutes=BEFORE)).strftime(TIME_FMT)
    if remind_at >= now_str(now):
        conn.execute("INSERT INTO reminders (room_id, remind_at, kind) VALUES (?, ?, 'before')", (room_id, remind_at))


#改期用：刪掉這個房間所有提醒。不 commit
def clear_reminders(conn, room_id):
    conn.execute("DELETE FROM reminders WHERE room_id=?", (room_id,))


#到期該發的提醒（附 start_at = 開打時間）；開打時間已經過了的（例如 bot 關機錯過）直接標 sent，不回傳
def due_reminders(conn, now=None):
    now_s = now_str(now)
    rows = conn.execute(
        "SELECT reminders.*, rooms.decided_at AS start_at FROM reminders "
        "JOIN rooms ON rooms.id = reminders.room_id "
        "WHERE reminders.sent=0 AND reminders.remind_at <= ? ORDER BY reminders.remind_at, reminders.id",
        (now_s,),
    ).fetchall()
    due = []
    for r in rows:
        if r["start_at"] is None or r["start_at"] <= now_s:
            mark_sent(conn, r["id"])
        else:
            due.append(r)
    return due


def mark_sent(conn, reminder_id):
    conn.execute("UPDATE reminders SET sent=1 WHERE id=?", (reminder_id,))
    conn.commit()
