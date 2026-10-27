from datetime import datetime, timedelta

from rooms import TIME_FMT, now_str, range_length

DATE_FMT = "%Y-%m-%d"
STEP = 30          # 一格幾分鐘
NIGHTS = 7         # 可以選今天起幾天


#今天起 7 天的日期，給選單用
def night_options(today):
    return [(today + timedelta(days=i)).strftime(DATE_FMT) for i in range(NIGHTS)]


#那一晚所有開始時間（不管有沒有過去）；range_start 到 range_end 頭尾都算，跨午夜的日期 +1
def all_start_times(room, night):
    first = datetime.strptime(f"{night} {room['range_start']}", TIME_FMT)
    steps = range_length(room["range_start"], room["range_end"]) // STEP
    return [(first + timedelta(minutes=STEP * i)).strftime(TIME_FMT) for i in range(steps + 1)]


#start_at 屬於哪一晚（跨午夜的時段算前一晚）；不是這個房間的開始時間 → None
def night_of(room, start_at):
    day = datetime.strptime(start_at, TIME_FMT)
    for d in (day, day - timedelta(days=1)):
        night = d.strftime(DATE_FMT)
        if start_at in all_start_times(room, night):
            return night
    return None


#那一晚還沒過去的開始時間（最多 25 個）
def start_times(room, night, now=None):
    now_s = now_str(now)
    return [s for s in all_start_times(room, night) if s >= now_s]   # 同格式字串可以直接比大小


#顯示用："20:00–22:00"
def slot_label(start_at, duration):
    start = datetime.strptime(start_at, TIME_FMT)
    end = start + timedelta(hours=duration)
    return f"{start:%H:%M}–{end:%H:%M}"


#多晚一起填：這幾晚裡至少一晚還能選的開始時間，只取 "HH:MM"（照一晚裡的順序，跨午夜的排後面）
def times_of_day(room, nights, now=None):
    if not nights:
        return []
    open_times = {s[-5:] for n in nights for s in start_times(room, n, now)}
    return [s[-5:] for s in all_start_times(room, nights[0]) if s[-5:] in open_times]
