import pytest

from db import connect, init_db


@pytest.fixture
def conn():
    conn = connect(":memory:")   # 每個測試一個全新的資料庫
    init_db(conn)
    yield conn
    conn.close()
