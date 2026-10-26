def parse_guild_ids(text):
    """"111,222, 333" → {111, 222, 333}；空的或不是數字 → ValueError"""
    ids = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise ValueError(f"伺服器 ID 不是數字：{part}")
        ids.add(int(part))
    if not ids:
        raise ValueError("GUILD_IDS 至少要一個伺服器 ID")
    return ids
