import os
from datetime import datetime

import discord
from discord import app_commands
from discord.utils import MISSING
from dotenv import load_dotenv

from db import connect, init_db
from rooms import TIME_FMT, TZ, create_room, get_room, is_leader, join_room, member_ids, user_rooms
from schedule import decide, get_availability, ranking, set_availability
from slots import DATE_FMT, night_options, slot_label, start_times

load_dotenv()                                    # 讀env
TOKEN = os.environ["DISCORD_TOKEN"]
GUILD = discord.Object(id=int(os.environ["GUILD_ID"]))

conn = connect("boss.db")                        # 整個 bot 共用一條連線
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
        # 連上 Discord 前跑一次：把指令同步到測試伺服器
        self.tree.copy_global_to(guild=GUILD)
        await self.tree.sync(guild=GUILD)


bot = Bot()


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


#私訊；對方關掉私訊（Forbidden）→ 改在房間頻道 @ 他
async def notify(room, user_id, text):
    try:
        user = await bot.fetch_user(user_id)
        await user.send(text, allowed_mentions=NO_PING)
    except discord.Forbidden:
        channel = await room_channel(room)
        await channel.send(
            f"<@{user_id}> {text}",
            allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=user_id)]),
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
        await show(interaction, *self.step(room, interaction.user.id))


#只有一個房間 → 直接下一步；多個 → 先選房間
async def with_room(interaction, rooms, step, empty_text):
    if not rooms:
        await show(interaction, empty_text)
    elif len(rooms) == 1:
        await show(interaction, *step(rooms[0], interaction.user.id))
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


@bot.tree.command(name="建房", description="建立打王房間")
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
    await notify(room, interaction.user.id, f"你加入了打王房間\n{room_info(room)}\n用 /填時間 填可以的時間")


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
        channel = await room_channel(room)
        await channel.send(text, allowed_mentions=NO_PING)
        for user_id in member_ids(conn, room["id"]):
            await notify(room, user_id, text)


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
async def result(interaction: discord.Interaction):
    rooms = user_rooms(conn, interaction.user.id)
    await with_room(interaction, rooms, result_step, "你還沒加入任何房間")


# ---------- 其他 ----------

@bot.tree.command(description="測試 bot 有沒有活著")
async def ping(interaction: discord.Interaction):
    ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"pong！延遲 {ms} ms")

@bot.tree.command(description="打招呼")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"Hello!{interaction.user.display_name}")

@bot.event
async def on_ready():
    print(f"已登入：{bot.user}")


if __name__ == "__main__":
    bot.run(TOKEN)
