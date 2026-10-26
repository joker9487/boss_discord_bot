import inspect
import os
import traceback
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import tasks
from discord.utils import MISSING
from dotenv import load_dotenv

from db import connect, init_db
from guilds import parse_guild_ids
from reminders import BEFORE, due_reminders, mark_sent
from rooms import (
    TIME_FMT, TZ, cleanup, create_room, delete_room, get_room, get_room_by_id, is_leader, join_room, kick,
    leave_room, member_ids, user_rooms,
)
from schedule import decide, get_availability, ranking, set_availability
from slots import DATE_FMT, night_options, slot_label, start_times

load_dotenv()                                    # 讀env
TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_IDS = parse_guild_ids(os.environ["GUILD_IDS"])   # 白名單：只服務這些伺服器

conn = connect(os.getenv("DB_PATH", "boss.db"))  # 整個 bot 共用一條連線；Docker 裡指到 volume
init_db(conn)

NO_PING = discord.AllowedMentions.none()         # 顯示 <@id> 但不通知
WEEKDAYS = "一二三四五六日"


class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)   # 存放所有斜線指令

    async def setup_hook(self):
        # 公告的「加入」、結果的「定案」按鈕：bot 重開後還能按
        self.add_dynamic_items(JoinButton, DecideButton)
        # 連上 Discord 前跑一次：全域指令（伺服器 + 私訊）同步一次；
        # 每個白名單伺服器只放 /建房（不用 copy_global_to，不然伺服器裡會有兩份；舊的複製版會被這次覆蓋掉）
        await self.tree.sync()
        for guild_id in GUILD_IDS:
            guild = discord.Object(id=guild_id)
            try:
                await self.tree.sync(guild=guild)
            except discord.HTTPException as e:       # bot 還沒加入這個伺服器等 → 跳過，不影響其他
                print(f"伺服器 {guild_id} 同步指令失敗：{e}")
        every_minute.start()                         # 每分鐘：發提醒、自動清理


bot = Bot()


#全域指令：伺服器和私訊都能用；只能裝在伺服器（不開放個人安裝）
def dm_ok(func):
    func = app_commands.allowed_contexts(guilds=True, dms=True, private_channels=False)(func)
    return app_commands.allowed_installs(guilds=True, users=False)(func)


# ---------- 顯示 ----------

def night_label(night):
    d = datetime.strptime(night, DATE_FMT)
    return f"{d.month}/{d.day}（{WEEKDAYS[d.weekday()]}）"


#"10/29 21:00–23:00"
def slot_text(start_at, duration):
    d = datetime.strptime(start_at, TIME_FMT)
    return f"{d.month}/{d.day} {slot_label(start_at, duration)}"


def room_info(room):
    return (
        f"**{room['name']}**\n"
        f"隊長：<@{room['leader_id']}>\n"
        f"時長：{room['duration']} 小時\n"
        f"開始時間範圍：{room['range_start']}～{room['range_end']}\n"
        f"key：`{room['key']}`"
    )


def new_view():
    return discord.ui.View(timeout=None)


#第一步 → 送出 ephemeral；之後 → 編輯同一則訊息
async def show(interaction, content, view=None):
    if interaction.type == discord.InteractionType.component:
        await interaction.response.edit_message(content=content, view=view, allowed_mentions=NO_PING)
    else:
        await interaction.response.send_message(
            content, view=view if view is not None else MISSING, ephemeral=True, allowed_mentions=NO_PING,
        )


# ---------- 通知 ----------

async def room_channel(room):
    return bot.get_channel(room["channel_id"]) or await bot.fetch_channel(room["channel_id"])


#在房間頻道貼訊息；失敗只記 log，不丟出去
async def post_channel(room, text, allowed_mentions=NO_PING):
    try:
        channel = await room_channel(room)
        await channel.send(text, allowed_mentions=allowed_mentions)
    except discord.HTTPException as e:
        print(f"頻道 {room['channel_id']} 發送失敗：{e}")


#私訊；失敗（HTTPException，含對方關掉私訊的 Forbidden）→ 改在房間頻道 @ 他；不會丟出去，一個人失敗不影響其他人
#hint=True：私訊結尾提示可以直接在私訊打指令（改發到頻道時不加）
DM_HINT = "\n可以直接在這裡打 /填時間、/結果"


async def notify(room, user_id, text, hint=False):
    try:
        user = await bot.fetch_user(user_id)
        await user.send(text + (DM_HINT if hint else ""), allowed_mentions=NO_PING)
    except discord.HTTPException:
        await post_channel(
            room, f"<@{user_id}> {text}", discord.AllowedMentions(users=[discord.Object(id=user_id)]),
        )


# ---------- 選房間（共用） ----------

class RoomSelect(discord.ui.Select):
    def __init__(self, rooms, step):
        options = [
            discord.SelectOption(label=r["name"][:100], description=f"key {r['key']}", value=r["key"])
            for r in rooms[:25]
        ]
        super().__init__(placeholder="選房間", options=options)
        self.step = step

    async def callback(self, interaction):
        room = get_room(conn, self.values[0])
        if room is None:
            await show(interaction, "這個房間已經不在了")
            return
        await show(interaction, *await run_step(self.step, room, interaction.user.id))


#step 可以是一般函式或 async（/踢人 要查名字）
async def run_step(step, room, user_id):
    result = step(room, user_id)
    return await result if inspect.isawaitable(result) else result


#只有一個房間 → 直接下一步；多個 → 先選房間
async def with_room(interaction, rooms, step, empty_text):
    if not rooms:
        await show(interaction, empty_text)
    elif len(rooms) == 1:
        await show(interaction, *await run_step(step, rooms[0], interaction.user.id))
    else:
        view = new_view()
        view.add_item(RoomSelect(rooms, step))
        await show(interaction, "選房間：", view)


# ---------- /建房 ----------

class CreateRoomModal(discord.ui.Modal, title="建房"):
    name = discord.ui.Label(text="名稱", component=discord.ui.TextInput(max_length=50))
    duration = discord.ui.Label(
        text="時長（小時）", description="1～3",
        component=discord.ui.TextInput(placeholder="2", max_length=1),
    )
    range_start = discord.ui.Label(
        text="最早開始（HH:MM）", description="整點或半點",
        component=discord.ui.TextInput(placeholder="18:00", min_length=5, max_length=5),
    )
    range_end = discord.ui.Label(
        text="最晚開始（HH:MM）", description="比最早開始小代表跨午夜，範圍最長 12 小時",
        component=discord.ui.TextInput(placeholder="02:00", min_length=5, max_length=5),
    )

    def __init__(self):
        super().__init__(timeout=None)

    async def on_submit(self, interaction):
        try:
            try:
                duration = int(self.duration.component.value)
            except ValueError:
                raise ValueError("時長要是 1～3 的整數")
            key = create_room(
                conn, self.name.component.value.strip(), interaction.user.id, interaction.channel_id,
                duration, self.range_start.component.value, self.range_end.component.value,
            )
        except ValueError as e:
            await interaction.response.send_message(f"建房失敗：{e}", ephemeral=True)
            return

        room = get_room(conn, key)
        await interaction.response.send_message(f"建好了！key：`{key}`（把 key 給隊員，或請他們按公告的「加入」）", ephemeral=True)
        view = new_view()
        view.add_item(JoinButton(key))
        await interaction.channel.send(f"📢 新的打王房間\n{room_info(room)}", view=view, allowed_mentions=NO_PING)


#只登記在白名單伺服器：公告要貼在建房的頻道
@bot.tree.command(name="建房", description="建立打王房間", guilds=[discord.Object(id=g) for g in GUILD_IDS])
async def create(interaction: discord.Interaction):
    await interaction.response.send_modal(CreateRoomModal())


# ---------- /加入、「加入」按鈕 ----------

async def do_join(interaction, key):
    key = key.strip().upper()
    room = get_room(conn, key)
    if room is None:
        await interaction.response.send_message(f"找不到 key `{key}` 的房間", ephemeral=True)
        return
    if interaction.user.id in member_ids(conn, room["id"]):
        await interaction.response.send_message(f"你已經在「{room['name']}」裡了", ephemeral=True)
        return

    join_room(conn, key, interaction.user.id)
    await interaction.response.send_message(f"已加入「{room['name']}」，用 /填時間 填可以的時間", ephemeral=True)
    await notify(room, interaction.user.id, f"你加入了打王房間\n{room_info(room)}\n用 /填時間 填可以的時間", hint=True)


class JoinButton(discord.ui.DynamicItem[discord.ui.Button], template=r"join:(?P<key>[A-Z0-9]{6})"):
    def __init__(self, key):
        super().__init__(discord.ui.Button(label="加入", style=discord.ButtonStyle.primary, custom_id=f"join:{key}"))
        self.key = key

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["key"])

    async def callback(self, interaction):
        await do_join(interaction, self.key)


@bot.tree.command(name="加入", description="用 key 加入打王房間")
@app_commands.describe(key="房間的 6 碼 key")
@dm_ok
async def join(interaction: discord.Interaction, key: str):
    await do_join(interaction, key)


# ---------- /填時間：選房間 → 選哪一晚 → 多選開始時間 ----------

class NightSelect(discord.ui.Select):
    def __init__(self, key, nights):
        options = [discord.SelectOption(label=night_label(n), value=n) for n in nights]
        super().__init__(placeholder="選哪一晚", options=options)
        self.key = key

    async def callback(self, interaction):
        room = get_room(conn, self.key)
        if room is None:
            await show(interaction, "這個房間已經不在了")
            return
        await show(interaction, *times_step(room, self.values[0], interaction.user.id))


class TimeSelect(discord.ui.Select):
    def __init__(self, room, night, times, picked):
        options = [
            discord.SelectOption(label=slot_label(s, room["duration"]), value=s, default=s in picked)
            for s in times
        ]
        super().__init__(placeholder="可以開始的時間（可多選）", options=options, min_values=0, max_values=len(options))
        self.key = room["key"]
        self.night = night

    async def callback(self, interaction):
        room = get_room(conn, self.key)
        if room is None:
            await show(interaction, "這個房間已經不在了")
            return
        try:
            set_availability(conn, room["id"], interaction.user.id, self.night, self.values, datetime.now(TZ))
        except ValueError as e:
            await show(interaction, f"儲存失敗：{e}")
            return
        content, view = times_step(room, self.night, interaction.user.id)
        await show(interaction, f"{content}\n✅ 已儲存（{len(self.values)} 個時段）", view)


def night_step(room, user_id):
    now = datetime.now(TZ)
    nights = [n for n in night_options(now.date()) if start_times(room, n, now)]
    if not nights:
        return f"「{room['name']}」這一週已經沒有可以選的時間了", None
    view = new_view()
    view.add_item(NightSelect(room["key"], nights))
    return f"「{room['name']}」要填哪一晚？", view


def times_step(room, night, user_id):
    times = start_times(room, night, datetime.now(TZ))
    if not times:
        return f"「{room['name']}」{night_label(night)} 的時間都過了", None
    picked = set(get_availability(conn, room["id"], user_id, night))
    view = new_view()
    view.add_item(TimeSelect(room, night, times, picked))
    return f"「{room['name']}」{night_label(night)}：勾可以開始的時間（全部取消 = 這晚都不行）", view


@bot.tree.command(name="填時間", description="填你可以開始的時間")
@dm_ok
async def fill(interaction: discord.Interaction):
    rooms = [r for r in user_rooms(conn, interaction.user.id) if r["status"] == "open"]
    await with_room(interaction, rooms, night_step, "你沒有還沒定案的房間")


# ---------- /結果、「定案」按鈕 ----------

class DecideButton(discord.ui.DynamicItem[discord.ui.Button], template=r"decide:(?P<key>[A-Z0-9]{6}):(?P<start>\d{12})"):
    def __init__(self, key, start_at):
        d = datetime.strptime(start_at, TIME_FMT)
        super().__init__(discord.ui.Button(
            label=f"定案 {d.month}/{d.day} {d:%H:%M}", style=discord.ButtonStyle.success,
            custom_id=f"decide:{key}:{d:%Y%m%d%H%M}",     # custom_id 只能 100 字，時間壓成 12 碼
        ))
        self.key = key
        self.start_at = start_at

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        start_at = datetime.strptime(match["start"], "%Y%m%d%H%M").strftime(TIME_FMT)
        return cls(match["key"], start_at)

    async def callback(self, interaction):
        room = get_room(conn, self.key)
        if room is None:
            await interaction.response.send_message("這個房間已經不在了", ephemeral=True)
            return
        try:
            decide(conn, room["id"], interaction.user.id, self.start_at, datetime.now(TZ))
        except ValueError as e:
            await interaction.response.send_message(f"定案失敗：{e}", ephemeral=True)
            return

        when = slot_text(self.start_at, room["duration"])
        await interaction.response.edit_message(content=f"「{room['name']}」已定案：{when}", view=None)
        text = f"🎯「{room['name']}」定案：{when}（隊長 <@{room['leader_id']}>）"
        await post_channel(room, text)
        for user_id in member_ids(conn, room["id"]):
            await notify(room, user_id, text, hint=True)


def result_step(room, user_id):
    if room["status"] == "decided":
        return f"「{room['name']}」已定案：{slot_text(room['decided_at'], room['duration'])}", None

    result = ranking(conn, room["id"], top=5, now=datetime.now(TZ))
    if not result["slots"]:
        return f"「{room['name']}」還沒有人填（還沒過去的）時間", None

    members = member_ids(conn, room["id"])
    view = new_view() if is_leader(room, user_id) else None
    lines = []
    for i, s in enumerate(result["slots"], 1):
        missing = [m for m in members if m not in s["user_ids"]]
        who = "全員" if not missing else "缺：" + " ".join(f"<@{m}>" for m in missing)
        lines.append(f"{i}. {slot_text(s['start_at'], room['duration'])}　{s['count']}/{result['members']} 人（{who}）")
        if view is not None:
            view.add_item(DecideButton(room["key"], s["start_at"]))
    return f"「{room['name']}」前 {len(lines)} 名：\n" + "\n".join(lines), view


@bot.tree.command(name="結果", description="看大家可以的時間（隊長可以定案）")
@dm_ok
async def result(interaction: discord.Interaction):
    rooms = user_rooms(conn, interaction.user.id)
    await with_room(interaction, rooms, result_step, "你還沒加入任何房間")


# ---------- 管理：/退出、/踢人、/關房、/我的房間 ----------

class ConfirmButton(discord.ui.Button):
    def __init__(self, label, action):
        super().__init__(label=label, style=discord.ButtonStyle.danger)
        self.action = action

    async def callback(self, interaction):
        await self.action(interaction)


def confirm_view(label, action):
    view = new_view()
    view.add_item(ConfirmButton(label, action))
    return view


def leave_step(room, user_id):
    key = room["key"]

    async def action(interaction):
        room = get_room(conn, key)
        if room is None:
            await show(interaction, "這個房間已經不在了")
            return
        try:
            leave_room(conn, room["id"], interaction.user.id)
        except ValueError as e:
            await show(interaction, f"退出失敗：{e}")
            return
        await show(interaction, f"已退出「{room['name']}」")
        await notify(room, room["leader_id"], f"{interaction.user.display_name}（<@{interaction.user.id}>）退出了「{room['name']}」")

    return f"確定要退出「{room['name']}」？你填的時間會一起刪掉", confirm_view("確定退出", action)


@bot.tree.command(name="退出", description="退出房間（隊長不能退出，請用 /關房）")
@dm_ok
async def leave(interaction: discord.Interaction):
    rooms = [r for r in user_rooms(conn, interaction.user.id) if not is_leader(r, interaction.user.id)]
    await with_room(interaction, rooms, leave_step, "你沒有可以退出的房間（隊長不能退出）")


#列房間成員（不用 UserSelect：私訊裡它只選得到私訊裡的人）
class KickSelect(discord.ui.Select):
    def __init__(self, key, members):
        options = [discord.SelectOption(label=name[:100], value=str(uid)) for uid, name in members[:25]]
        super().__init__(placeholder="選要踢的人", options=options)
        self.key = key

    async def callback(self, interaction):
        room = get_room(conn, self.key)
        if room is None:
            await show(interaction, "這個房間已經不在了")
            return
        target_id = int(self.values[0])
        try:
            kick(conn, room["id"], interaction.user.id, target_id)
        except ValueError as e:
            await show(interaction, f"踢人失敗：{e}，請重選", self.view)
            return
        await show(interaction, f"已把 <@{target_id}> 移出「{room['name']}」")
        await notify(room, target_id, f"你被隊長移出了「{room['name']}」")


async def display_name(user_id):
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        return user.display_name
    except discord.HTTPException:
        return str(user_id)


async def kick_step(room, user_id):
    others = [m for m in member_ids(conn, room["id"]) if m != user_id]
    if not others:
        return f"「{room['name']}」除了你沒有其他成員", None
    members = [(m, await display_name(m)) for m in others]
    view = new_view()
    view.add_item(KickSelect(room["key"], members))
    return f"「{room['name']}」要踢誰？", view


@bot.tree.command(name="踢人", description="隊長把人移出房間")
@dm_ok
async def kick_member(interaction: discord.Interaction):
    rooms = [r for r in user_rooms(conn, interaction.user.id) if is_leader(r, interaction.user.id)]
    await with_room(interaction, rooms, kick_step, "你不是任何房間的隊長")


def close_step(room, user_id):
    key = room["key"]

    async def action(interaction):
        room = get_room(conn, key)
        if room is None:
            await show(interaction, "這個房間已經不在了")
            return
        if not is_leader(room, interaction.user.id):
            await show(interaction, "只有隊長能關房")
            return
        members = member_ids(conn, room["id"])
        delete_room(conn, room["id"])
        await show(interaction, f"已關閉「{room['name']}」")       # 先回應（3 秒限制），再一個一個私訊
        for user_id in members:
            await notify(room, user_id, f"「{room['name']}」已關閉")

    return f"確定要關閉「{room['name']}」？成員、填的時間、提醒會全部刪掉", confirm_view("確定關房", action)


@bot.tree.command(name="關房", description="隊長關閉房間")
@dm_ok
async def close(interaction: discord.Interaction):
    rooms = [r for r in user_rooms(conn, interaction.user.id) if is_leader(r, interaction.user.id)]
    await with_room(interaction, rooms, close_step, "你不是任何房間的隊長")


@bot.tree.command(name="我的房間", description="列出你在的房間")
@dm_ok
async def my_rooms(interaction: discord.Interaction):
    rooms = user_rooms(conn, interaction.user.id)
    if not rooms:
        await show(interaction, "你還沒加入任何房間")
        return
    lines = []
    for r in rooms:
        role = "隊長" if is_leader(r, interaction.user.id) else "成員"
        status = f"已定案 {slot_text(r['decided_at'], r['duration'])}" if r["status"] == "decided" else "填寫中"
        lines.append(f"**{r['name']}**　`{r['key']}`｜{role}｜{status}")
    await show(interaction, "\n".join(lines))


# ---------- 每分鐘：提醒、自動清理 ----------

REMIND_PREFIX = {"day": "📅 今天要打王", "before": f"⏰ {BEFORE} 分鐘後開打"}


@tasks.loop(minutes=1)
async def every_minute():
    try:
        now = datetime.now(TZ)
        for r in due_reminders(conn, now):
            room = get_room_by_id(conn, r["room_id"])
            text = f"{REMIND_PREFIX[r['kind']]}：「{room['name']}」{slot_text(r['start_at'], room['duration'])}"
            for user_id in member_ids(conn, room["id"]):
                await notify(room, user_id, text, hint=True)
            mark_sent(conn, r["id"])
        deleted = cleanup(conn, now)
        if deleted:
            print(f"自動清理：刪了 {deleted} 間房")
    except Exception:
        traceback.print_exc()        # 迴圈遇到沒接住的錯誤會停掉，所以這裡全部接住


@every_minute.before_loop
async def before_every_minute():
    await bot.wait_until_ready()


# ---------- 其他 ----------

@bot.tree.command(description="測試 bot 有沒有活著")
@dm_ok
async def ping(interaction: discord.Interaction):
    ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"pong！延遲 {ms} ms")

@bot.tree.command(description="打招呼")
@dm_ok
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"Hello!{interaction.user.display_name}")

async def leave_if_not_allowed(guild):
    if guild.id not in GUILD_IDS:
        print(f"不在白名單，退出伺服器：{guild.name}（{guild.id}）")
        await guild.leave()


@bot.event
async def on_guild_join(guild):
    await leave_if_not_allowed(guild)


@bot.event
async def on_ready():
    print(f"已登入：{bot.user}")
    # 關機期間被加進去的伺服器也要退出
    for guild in bot.guilds:
        await leave_if_not_allowed(guild)


if __name__ == "__main__":
    bot.run(TOKEN)
