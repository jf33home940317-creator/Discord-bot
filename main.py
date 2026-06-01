import os
import asyncio
import logging
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s: %(message)s',
)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'Bot 已上線：{bot.user}')

@bot.tree.command(name='help', description='顯示所有指令說明')
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title='🎵 音樂 Bot 指令', color=discord.Color.blue())
    commands_list = [
        ('/play <歌名或URL>', '播放音樂（支援 YouTube / Spotify / YouTube 播放清單）'),
        ('/skip', '跳過目前這首'),
        ('/stop', '停止播放並離開語音頻道'),
        ('/queue [頁碼]', '顯示播放清單（每頁 10 首）'),
        ('/pause', '暫停播放'),
        ('/resume', '繼續播放'),
        ('/nowplaying', '顯示目前播放的歌曲'),
        ('/loop', '切換循環播放模式 🔁'),
        ('/shuffle', '隨機洗牌播放清單 🔀'),
        ('/remove <編號>', '刪除播放清單中指定歌曲'),
        ('/autoplay', '切換自動推薦播放（佇列結束時自動接歌）🎶'),
        ('/help', '顯示此說明'),
    ]
    for cmd, desc in commands_list:
        embed.add_field(name=cmd, value=desc, inline=False)
    await interaction.response.send_message(embed=embed)

async def main():
    async with bot:
        await bot.load_extension('music')
        await bot.start(os.getenv('DISCORD_TOKEN'))

asyncio.run(main())
