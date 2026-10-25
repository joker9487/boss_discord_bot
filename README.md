# boss_discord_bot

![test](https://github.com/joker9487/boss_discord_bot/actions/workflows/test.yml/badge.svg)

Discord 打王約時間 bot。隊長建房、隊員用 key 加入，每個人勾自己可以開始的時間，bot 整理出全員都能到的時段，隊長定案後自動私訊提醒。

核心邏輯（db / rooms / slots / schedule / reminders）和 Discord 層（bot.py）分開，核心不 import discord，可以直接用 pytest 測。

## 功能

| 功能 | 說明 | 狀態 |
|---|---|---|
| 建房 | 名稱、總時長 1～3 小時、開始時間範圍（`HH:MM`，整點或半點，最長 12 小時，可跨午夜）→ 產生不重複的 6 碼 key，隊長自動加入 | ✅ 核心完成（Discord 指令開發中） |
| 加入 | 輸入 key 加入房間，同一人重複加入只算一次 | ✅ 核心完成（Discord 指令開發中） |
| 權限 | 只有隊長能定案、關房 | ✅ 核心完成（Discord 指令開發中） |
| 關房 | 刪除房間，成員、時間、提醒一起刪 | ✅ 核心完成（Discord 指令開發中） |
| 填時間 | 選日期（一週內）→ 多選可以開始的時間，30 分一格、可跨日、可重填覆蓋；已經過去的時間不列 | ✅ 核心完成（Discord 選單開發中） |
| 整合 | 依「全員可以 → 人多 → 時間早」排序，隊長定案，定案後鎖住 | ✅ 核心完成（Discord 按鈕開發中） |
| 提醒 | 當天 12:00、開始前 10 分鐘私訊；私訊被擋改在頻道 @ | 開發中 |
| 自動清理 | 定案時間過 24 小時、或建房 7 天沒定案自動刪除 | 開發中 |
| `/ping`、`/hello` | 測試 bot 有沒有活著 | ✅ |

## 資料表設計（SQLite）

```
rooms ─┬─< members
       ├─< availability
       └─< reminders
```

| 表 | 欄位 | 說明 |
|---|---|---|
| `rooms` | `id`、`key`（UNIQUE）、`name`、`leader_id`、`channel_id`、`duration`、`range_start`、`range_end`、`status`（`open` / `decided`）、`decided_at`、`created_at` | 一個房間一列；`channel_id` 用來在私訊失敗時改到頻道 @ |
| `members` | `room_id`、`user_id`，主鍵 `(room_id, user_id)` | 主鍵擋掉同一人重複加入 |
| `availability` | `room_id`、`user_id`、`start_at`，主鍵 `(room_id, user_id, start_at)` | `start_at` 存完整日期時間，跨日不會搞混 |
| `reminders` | `id`、`room_id`、`remind_at`、`kind`（`day` / `before`）、`sent` | 一律從定案時間算出來 |

- 三張子表的 `room_id` 都是 `REFERENCES rooms(id) ON DELETE CASCADE`：刪掉房間，成員、填的時間、提醒會一起被刪
- SQLite 外鍵預設是關的，`db.connect()` 每次連線都會執行 `PRAGMA foreign_keys = ON`
- 時間一律 Asia/Taipei，存成 `"YYYY-MM-DD HH:MM"` 字串

## 專案結構

```
├── bot.py          # Discord 層：只收指令、顯示
├── db.py           # 建表、連線
├── rooms.py        # 建房（含範圍檢查）、key、加入、權限、關房
├── slots.py        # 可選的日期、那一晚的開始時間（30 分、跨日、過去的不列）、時段顯示
├── schedule.py     # 填寫 / 覆蓋、整合排序、定案
├── reminders.py    # 開發中：定案 → 算提醒、查到期的
└── tests/
```

## 設定

需要 Python 3.13。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

在專案根目錄建 `.env`（已在 `.gitignore`，不會上傳）：

```
DISCORD_TOKEN=你的 bot token
GUILD_ID=測試伺服器 ID
```

- `.env` 格式是 `KEY=value`，不用加引號，`=` 兩邊不要有空格
- `DISCORD_TOKEN`：Developer Portal → Bot 頁 → Reset Token（不是 OAuth2 頁的 Client Secret）
- `GUILD_ID`：Discord 開「開發者模式」→ 對伺服器按右鍵 → 複製伺服器 ID。斜線指令只同步到這個伺服器，同步完馬上生效
- 邀請 bot：OAuth2 URL 勾 scope `bot` + `applications.commands`，permissions `3072`（View Channels + Send Messages）

## 跑 bot

```powershell
python bot.py
```

看到 `已登入：<bot 名稱>` 就是連上了。在伺服器打 `/ping` 測試。停止用 Ctrl+C；改了程式碼要重開才會生效。

## 跑測試

```powershell
pytest
```

測試用 `connect(":memory:")`，不會碰到真的資料庫檔。push 和 PR 時 GitHub Actions 會在 ubuntu + Python 3.13 自動跑一次（`.github/workflows/test.yml`）。
