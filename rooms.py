import secrets
import sqlite3
import string
from datetime import datetime
from zoneinfo import ZoneInfo



TZ = ZoneInfo("Asia/Taipei")
KEY_CHARS = string.ascii_uppercase + string.digits     # A~Z + 0~9
KEY_RETRY = 10                                         # key 最多重抽幾次


def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M")   # 統一的時間格式


def generate_key(length=6):
    return "".join(secrets.choice(KEY_CHARS) for _ in range(length))


def create_room(conn, name, leader_id, channel_id, duration, range_start, range_end):
    if not 1<= duration<=3:
         raise ValueError("時長要 1～3 小時")

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



#刪除房間
def delete_room(conn, room_id):
     conn.execute("DELETE FROM rooms WHERE id =?", (room_id,))
     conn.commit()
