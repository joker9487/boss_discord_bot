# boss_discord_bot

![test](https://github.com/joker9487/boss_discord_bot/actions/workflows/test.yml/badge.svg)

Discord 打王約時間 bot。隊長建房、隊員用 key 加入，每個人勾自己可以開始的時間，bot 整理出全員都能到的時段，隊長定案後自動私訊提醒。

核心邏輯（db / rooms / slots / schedule / reminders）和 Discord 層（bot.py）分開，核心不 import discord，可以直接用 pytest 測。

## 功能

| 功能 | 指令 | 說明 | 狀態 |
|---|---|---|---|
| 建房 | `/建房` | 跳出表單：名稱、總時長 1～3 小時、開始時間範圍（`HH:MM`，整點或半點，最長 12 小時，可跨午夜）→ 產生不重複的 6 碼 key，隊長自動加入；頻道貼公告 +「加入」按鈕 | ✅ |
| 加入 | `/加入 key`、公告的「加入」按鈕 | 加入成功私訊通知；同一人重複加入只算一次 | ✅ |
| 填時間 | `/填時間` | 選房間 → 選哪一晚（一週內）→ 多選可以開始的時間（30 分一格、可跨日、預先勾好上次填的、可全部取消）；已經過去的時間不列 | ✅ |
| 整合 / 定案 | `/結果` | 前 5 名「10/29 21:00–23:00 4/5 人（缺：@誰）」，排序：人多 → 時間早，過去的不列；隊長每個時段有「定案」按鈕，定案後私訊每個成員 + 頻道公告，房間鎖住 | ✅ |
| 私訊失敗 | — | 私訊失敗（對方關掉私訊等）→ 改在建房的頻道 @ 他；一個人失敗不影響其他人 | ✅ |
| 退出 | `/退出` | 選房間 → 確定 → 退出，填的時間一起刪，私訊通知隊長；隊長不能退出 | ✅ |
| 踢人 | `/踢人` | 隊長選房間 → 選人 → 移出，私訊通知被踢的人 | ✅ |
| 關房 | `/關房` | 隊長選房間 → 確定 → 私訊所有成員「已關閉」，成員、時間、提醒一起刪 | ✅ |
| 我的房間 | `/我的房間` | 列出所在房間：key、是不是隊長、填寫中 / 已定案（定案時間） | ✅ |
| 提醒 | — | 定案時寫入：「那一晚」的 12:00（凌晨場算前一晚）、開始前 10 分鐘；已經過去的不寫。每分鐘檢查一次，私訊每個成員；bot 關機錯過、已經開打的不補發 | ✅ |
| 自動清理 | — | 每分鐘檢查：定案時間過 24 小時、或建房超過 7 天沒定案 → 刪除 | ✅ |
| 測試用 | `/ping`、`/hello` | 測試 bot 有沒有活著 | ✅ |

- 所有回覆都只有自己看得到（ephemeral），`/填時間`、`/結果` 每一步都更新同一則訊息；只在一個房間時跳過選房間
- 「加入」「定案」按鈕在 bot 重開後還能按

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
├── bot.py          # Discord 層：只收指令、顯示；資料庫 boss.db（啟動時自動建表）
├── db.py           # 建表、連線
├── guilds.py       # 解析 GUILD_IDS 白名單
├── rooms.py        # 建房（含範圍檢查）、key、加入、退出、踢人、權限、關房、自動清理
├── slots.py        # 可選的日期、那一晚的開始時間（30 分、跨日、過去的不列）、時段顯示
├── schedule.py     # 填寫 / 覆蓋、整合排序、定案
├── reminders.py    # 定案 → 寫提醒、查到期的、標已發送
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
GUILD_IDS=伺服器ID1,伺服器ID2
```

- `.env` 格式是 `KEY=value`，不用加引號，`=` 兩邊不要有空格
- `DISCORD_TOKEN`：Developer Portal → Bot 頁 → Reset Token（不是 OAuth2 頁的 Client Secret）
- `GUILD_IDS`：白名單，逗號分隔一個或多個伺服器 ID。Discord 開「開發者模式」→ 對伺服器按右鍵 → 複製伺服器 ID
  - 啟動時把斜線指令同步到每個白名單伺服器，同步完馬上生效；bot 還沒加入的伺服器會印一行「同步指令失敗」並跳過
  - bot 被邀進不在白名單的伺服器會自動退出（啟動時也會檢查一次），並印一行紀錄
  - 新增伺服器：把 ID 加進 `GUILD_IDS` → 重開 bot（Docker 用 `docker compose up -d`）→ 再邀請 bot。順序反過來的話，bot 會先被自動踢出去
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

測試用 `connect(":memory:")`，不會碰到真的資料庫檔。push 和 PR 時 GitHub Actions 會在 ubuntu + Python 3.13 自動跑一次（`.github/workflows/test.yml`），另外會 build 一次 Docker image 確認建得起來（不會跑起來，CI 沒有 token）。

## 用 Docker 跑

需要 Docker Desktop。`.env` 照上面「設定」建好，在專案根目錄：

```powershell
docker compose up -d --build   # 建 image 並在背景啟動
docker compose logs -f         # 看 log（看到「已登入」就是連上了），Ctrl+C 只是離開 log
docker compose down            # 停止並移除容器
```

- 改了程式碼要重建 image 才會生效：`docker compose up -d --build`（只改程式不會重裝套件，有快取）
- 資料庫在 `./data/boss.db`（掛進容器的 `/app/data`），`down` 再 `up` 房間都還在；`data/` 已在 `.gitignore`
- 容器時區是 Asia/Taipei；bot 掛掉、或 Docker Desktop 重新啟動後，會自動把它重啟（`restart: unless-stopped`），要停就用 `down`
- 不要同時用 `python bot.py` 和 Docker 跑，同一個 token 會有兩個 bot 搶著回應
