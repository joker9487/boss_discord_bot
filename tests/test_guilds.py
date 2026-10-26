import pytest

from guilds import parse_guild_ids


def test_one_id():
    assert parse_guild_ids("123") == {123}


def test_many_ids_with_spaces():
    assert parse_guild_ids("111, 222 ,333") == {111, 222, 333}


def test_duplicate_and_trailing_comma():
    assert parse_guild_ids("111,111,") == {111}


@pytest.mark.parametrize("text", ["", " ", ",", " , "])
def test_empty_raises(text):
    with pytest.raises(ValueError):
        parse_guild_ids(text)


@pytest.mark.parametrize("text", ["abc", "111,abc", "-5", "1.5"])
def test_not_number_raises(text):
    with pytest.raises(ValueError):
        parse_guild_ids(text)
