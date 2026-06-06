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

# 只用 slash commands，不需要特權的 message_content intent
intents = discord.Intents.default()

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'Bot 已上線：{bot.user}')

@bot.tree.command(name='help', description='顯示所有指令說明')
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title='🎵 佐為音樂 Bot', color=discord.Color.blue())

    # 播放
    embed.add_field(
        name='🎶 播放',
        value=(
            '`/play <歌名或URL>` — 播放音樂\n'
            '　支援 YouTube 單曲 / 播放清單 / Spotify 單曲\n'
            '`/playnext <歌名或URL>` — 插播，下一首優先播放\n'
            '`/skip` — 跳過目前這首\n'
            '`/stop` — 停止播放並離開語音頻道\n'
            '`/pause` — 暫停播放\n'
            '`/resume` — 繼續播放'
        ),
        inline=False,
    )

    # 佇列管理
    embed.add_field(
        name='📋 佇列管理',
        value=(
            '`/queue [頁碼]` — 顯示播放佇列（每頁 10 首）\n'
            '`/remove <編號>` — 刪除佇列中指定歌曲\n'
            '`/remove-playlist [編號]` — 移除整個 YT 播放清單\n'
            '　不填編號會列出所有清單供選擇\n'
            '`/shuffle` — 隨機洗牌佇列 🔀'
        ),
        inline=False,
    )

    # 播放模式
    embed.add_field(
        name='🔧 播放模式',
        value=(
            '`/loop` — 切換單曲循環 🔁\n'
            '`/autoplay` — 切換自動推薦（預設開啟）🎶\n'
            '　佇列結束時自動播放 YouTube 推薦歌曲'
        ),
        inline=False,
    )

    # 其他
    embed.add_field(
        name='ℹ️ 其他',
        value=(
            '`/nowplaying` — 顯示目前播放的歌曲（含控制按鈕）\n'
            '`/help` — 顯示此說明'
        ),
        inline=False,
    )

    embed.set_footer(text='播放時也可以直接用訊息下方的按鈕操作 ⏸️ ⏭️ 🔁 🔀 ⏹️')
    await interaction.response.send_message(embed=embed)

async def main():
    async with bot:
        await bot.load_extension('music')
        await bot.start(os.getenv('DISCORD_TOKEN'))

asyncio.run(main())
