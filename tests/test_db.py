def test_tables_created(conn):
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"rooms", "members", "availability", "reminders"} <= names


def test_foreign_keys_on(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_init_db_twice_ok(conn):
    from db import init_db
    init_db(conn)   # CREATE TABLE IF NOT EXISTS → 重跑不會壞
