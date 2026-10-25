import re
import secrets
import sqlite3
import string
from datetime import datetime
from zoneinfo import ZoneInfo



TZ = ZoneInfo("Asia/Taipei")
TIME_FMT = "%Y-%m-%d %H:%M"                            # 統一的時間格式
KEY_CHARS = string.ascii_uppercase + string.digits     # A~Z + 0~9
KEY_RETRY = 10                                         # key 最多重抽幾次
MAX_RANGE = 12 * 60                                    # 開始時間範圍最長 12 小時（分鐘）


#現在（或傳入的 now）轉成台北時間字串
def now_str(now=None):
    if now is None:
        now = datetime.now(TZ)
    return now.astimezone(TZ).strftime(TIME_FMT)


def generate_key(length=6):
    return "".join(secrets.choice(KEY_CHARS) for _ in range(length))


#"HH:MM" → 從 00:00 起算幾分鐘；格式錯或分鐘不是 00 / 30 → ValueError
def parse_hhmm(s):
    if not isinstance(s, str) or not re.fullmatch(r"\d{2}:\d{2}", s):
        raise ValueError(f"時間格式要是 HH:MM：{s!r}")
    hour, minute = int(s[:2]), int(s[3:])
    if hour > 23 or minute not in (0, 30):
        raise ValueError(f"時間要是整點或半點：{s!r}")
    return hour * 60 + minute


#範圍有幾分鐘；end <= start 代表跨午夜
def range_length(range_start, range_end):
    start, end = parse_hhmm(range_start), parse_hhmm(range_end)
    if end <= start:
        end += 24 * 60
    return end - start


def create_room(conn, name, leader_id, channel_id, duration, range_start, range_end):
    if not 1<= duration<=3:
         raise ValueError("時長要 1～3 小時")
    if range_length(range_start, range_end) > MAX_RANGE:
        raise ValueError("開始時間範圍最長 12 小時")

    for attempt in range(KEY_RETRY):
        key = generate_key()
        try:
            cur = conn.execute(
                "INSERT INTO rooms (key, name, leader_id, channel_id, duration, range_start, range_end, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (key, name, leader_id, channel_id, duration, range_start, range_end, now_str()),
            )
            break
        except sqlite3.IntegrityError as e:
            # 只有 key 撞到 UNIQUE 才重抽；其他錯（例如 name 是 None）或抽滿次數 → 直接丟出去
            if "rooms.key" not in str(e) or attempt == KEY_RETRY - 1:
                raise

    room_id = cur.lastrowid
    # 隊長加入隊伍
    conn.execute("INSERT INTO members (room_id, user_id) VALUES (?, ?)", (room_id, leader_id))
    conn.commit()
    return key

#用key 找 room
def get_room(conn, key):
    row = conn.execute("SELECT * FROM rooms WHERE key=?", (key,)).fetchone()
    return row

#user 加進 members
def join_room(conn, key, user_id):
    room = get_room(conn, key)
    if room is None:
        return None
    conn.execute("INSERT OR IGNORE INTO members (room_id, user_id) VALUES (?, ?)", (room["id"],user_id))
    conn.commit()
    return room


def is_leader(room, user_id):
    return user_id == room["leader_id"]


#這個人在的所有房間
def user_rooms(conn, user_id):
    return conn.execute(
        "SELECT rooms.* FROM rooms JOIN members ON members.room_id = rooms.id "
        "WHERE members.user_id=? ORDER BY rooms.id",
        (user_id,),
    ).fetchall()


#房間成員 id（照加入順序，隊長第一個）
def member_ids(conn, room_id):
    rows = conn.execute("SELECT user_id FROM members WHERE room_id=? ORDER BY rowid", (room_id,))
    return [r["user_id"] for r in rows]



#刪除房間
def delete_room(conn, room_id):
     conn.execute("DELETE FROM rooms WHERE id =?", (room_id,))
     conn.commit()
