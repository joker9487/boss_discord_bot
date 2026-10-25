import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    leader_id   INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    duration    INTEGER NOT NULL,          -- 小時，1~3
    range_start TEXT NOT NULL,             -- "18:00"
    range_end   TEXT NOT NULL,             -- "02:00"（可跨日）
    status      TEXT NOT NULL DEFAULT 'open',   -- open / decided
    decided_at  TEXT,                      -- 定案的開始時間，沒定案是 NULL
    created_at  TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS members (
    room_id     INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL,
    PRIMARY KEY (room_id, user_id)
);

CREATE TABLE IF NOT EXISTS availability (
    room_id     INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL,
    start_at    TEXT NOT NULL,
    PRIMARY KEY (room_id, user_id,start_at)
    
);

CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id     INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    remind_at   TEXT NOT NULL,  
    kind        TEXT NOT NULL,   -- 'day' / 'before'
    sent        INTEGER DEFAULT 0
);



"""


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()
    
if __name__ == "__main__":
    conn = connect(":memory:")
    init_db(conn)
    print([r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")])