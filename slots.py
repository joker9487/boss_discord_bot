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


#那一晚還沒過去的開始時間（最多 25 個）
def start_times(room, night, now=None):
    now_s = now_str(now)
    return [s for s in all_start_times(room, night) if s >= now_s]   # 同格式字串可以直接比大小


#顯示用："20:00–22:00"
def slot_label(start_at, duration):
    start = datetime.strptime(start_at, TIME_FMT)
    end = start + timedelta(hours=duration)
    return f"{start:%H:%M}–{end:%H:%M}"
