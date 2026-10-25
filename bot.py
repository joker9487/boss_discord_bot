import os
import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()                                    # 讀env
TOKEN = os.environ["DISCORD_TOKEN"]
GUILD = discord.Object(id=int(os.environ["GUILD_ID"]))


class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)   # 存放所有斜線指令

    async def setup_hook(self):
        # 連上 Discord 前跑一次：把指令同步到測試伺服器
        self.tree.copy_global_to(guild=GUILD)
        await self.tree.sync(guild=GUILD)


bot = Bot()


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




bot.run(TOKEN)