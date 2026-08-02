# Discord 音樂 Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一個 Discord 音樂 Bot，支援 YouTube / Spotify 播放、播放清單管理，部署到 Discloud 24 小時在線。

**Architecture:** `main.py` 負責 Bot 初始化與 `/help`，`music.py` 以 Cog 方式封裝所有音樂指令與佇列邏輯，yt-dlp 抓 YouTube 音訊，spotipy 解析 Spotify URL。

**Tech Stack:** Python 3.10+, discord.py 2.x, yt-dlp, spotipy, PyNaCl, python-dotenv, Discloud

---

## 檔案對應

| 檔案 | 職責 |
|---|---|
| `discord-bot/main.py` | Bot 初始化、載入 music Cog、`/help` 指令 |
| `discord-bot/music.py` | MusicCog：所有音樂指令、佇列、Spotify 解析 |
| `discord-bot/requirements.txt` | 套件清單 |
| `discord-bot/discloud.config` | Discloud 部署設定 |
| `discord-bot/.env` | API Token（本機用，不上傳） |

---

### Task 1: 建立專案結構與設定檔

**Files:**
- Create: `discord-bot/requirements.txt`
- Create: `discord-bot/discloud.config`
- Create: `discord-bot/.env`

- [ ] **Step 1: 建立 requirements.txt**

```
discord.py[voice]
yt-dlp
spotipy
PyNaCl
python-dotenv
```

- [ ] **Step 2: 建立 discloud.config**

```
ID=discord-music-bot
MAIN=main.py
MEMORY=256
VERSION=recommended
TYPE=bot
```

- [ ] **Step 3: 建立 .env（填入真實的 Token）**

```
DISCORD_TOKEN=你的_Discord_Bot_Token
SPOTIFY_CLIENT_ID=你的_Spotify_Client_ID
SPOTIFY_CLIENT_SECRET=你的_Spotify_Client_Secret
```

- [ ] **Step 4: 安裝套件**

```bash
cd E:\93050207\discord-bot
pip install -r requirements.txt
```

預期：所有套件安裝成功，無 error。

- [ ] **Step 5: 安裝 FFmpeg（音訊播放必要）**

前往 https://www.gyan.dev/ffmpeg/builds/ 下載 `ffmpeg-release-essentials.zip`，
解壓後將 `bin/ffmpeg.exe` 路徑加入系統環境變數 PATH。

驗證：
```bash
ffmpeg -version
```
預期：顯示 ffmpeg 版本資訊。

---

### Task 2: 申請 API Token

**Files:** 無（手動操作）

- [ ] **Step 1: 申請 Discord Bot Token**

1. 前往 https://discord.com/developers/applications
2. 點 「New Application」，輸入名稱（例如 MusicBot）
3. 左側選「Bot」→ 點「Reset Token」→ 複製 Token
4. 同頁面開啟「Message Content Intent」
5. 左側選「OAuth2」→「URL Generator」
   - Scopes 勾選：`bot`、`applications.commands`
   - Bot Permissions 勾選：`Send Messages`、`Connect`、`Speak`、`Use Slash Commands`
6. 複製生成的 URL，在瀏覽器開啟，把 Bot 加入你的伺服器
7. 將 Token 填入 `.env` 的 `DISCORD_TOKEN`

- [ ] **Step 2: 申請 Spotify API**

1. 前往 https://developer.spotify.com/dashboard
2. 點「Create app」，填入名稱、描述，Redirect URI 填 `http://localhost`
3. 建立後進入 App → 「Settings」→ 複製 Client ID 和 Client Secret
4. 填入 `.env` 的 `SPOTIFY_CLIENT_ID` 和 `SPOTIFY_CLIENT_SECRET`

---

### Task 3: 建立 main.py（Bot 初始化 + /help）

**Files:**
- Create: `discord-bot/main.py`

- [ ] **Step 1: 建立 main.py**

```python
import os
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

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
        ('/play <歌名或URL>', '播放音樂（支援 YouTube / Spotify）'),
        ('/skip', '跳過目前這首'),
        ('/stop', '停止播放並離開語音頻道'),
        ('/queue', '顯示播放清單'),
        ('/pause', '暫停播放'),
        ('/resume', '繼續播放'),
        ('/nowplaying', '顯示目前播放的歌曲'),
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
```

- [ ] **Step 2: 驗證 Bot 可以啟動**

```bash
cd E:\93050207\discord-bot
python main.py
```

預期輸出：`Bot 已上線：MusicBot#XXXX`

在 Discord 伺服器輸入 `/help`，應顯示指令說明的 embed 卡片。

---

### Task 4: 建立 music.py（MusicCog 骨架 + /play）

**Files:**
- Create: `discord-bot/music.py`

- [ ] **Step 1: 建立 music.py 骨架與 /play 指令**

```python
import os
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from collections import deque

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

YDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
}


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queues: dict[int, deque] = {}
        self.now_playing: dict[int, tuple[str, str]] = {}
        self.sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=os.getenv('SPOTIFY_CLIENT_ID'),
            client_secret=os.getenv('SPOTIFY_CLIENT_SECRET'),
        ))

    def get_queue(self, guild_id: int) -> deque:
        if guild_id not in self.queues:
            self.queues[guild_id] = deque()
        return self.queues[guild_id]

    def is_spotify_url(self, query: str) -> bool:
        return 'open.spotify.com/track' in query

    def spotify_to_search(self, url: str) -> str:
        track_id = url.split('/track/')[1].split('?')[0]
        track = self.sp.track(track_id)
        name = track['name']
        artist = track['artists'][0]['name']
        return f'{name} {artist}'

    def fetch_audio(self, query: str) -> tuple[str, str]:
        if self.is_spotify_url(query):
            query = self.spotify_to_search(query)
        if not query.startswith('http'):
            query = f'ytsearch:{query}'
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            info = ydl.extract_info(query, download=False)
            if 'entries' in info:
                info = info['entries'][0]
            return info['title'], info['url']

    def play_next(self, guild: discord.Guild):
        queue = self.get_queue(guild.id)
        vc = guild.voice_client
        if not vc:
            return
        if queue:
            title, url = queue.popleft()
            self.now_playing[guild.id] = (title, url)
            source = discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS)
            vc.play(source, after=lambda e: self.play_next(guild))
        else:
            self.now_playing.pop(guild.id, None)

    @app_commands.command(name='play', description='播放音樂（YouTube / Spotify 網址或歌名）')
    @app_commands.describe(query='歌名、YouTube 網址或 Spotify 網址')
    async def play(self, interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            await interaction.response.send_message('❌ 請先加入語音頻道！', ephemeral=True)
            return

        await interaction.response.defer()

        vc = interaction.guild.voice_client
        if vc is None:
            vc = await interaction.user.voice.channel.connect()
        elif vc.channel != interaction.user.voice.channel:
            await vc.move_to(interaction.user.voice.channel)

        loop = asyncio.get_running_loop()
        try:
            title, url = await loop.run_in_executor(None, self.fetch_audio, query)
        except Exception:
            await interaction.followup.send('❌ 找不到該歌曲，請換個關鍵字。')
            return

        queue = self.get_queue(interaction.guild_id)
        if vc.is_playing() or vc.is_paused():
            queue.append((title, url))
            await interaction.followup.send(f'✅ 已加入佇列：**{title}**')
        else:
            self.now_playing[interaction.guild_id] = (title, url)
            source = discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS)
            vc.play(source, after=lambda e: self.play_next(interaction.guild))
            await interaction.followup.send(f'▶️ 正在播放：**{title}**')


async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCog(bot))
```

- [ ] **Step 2: 測試 /play**

啟動 Bot，加入語音頻道後輸入：
```
/play Never Gonna Give You Up
```
預期：Bot 加入語音頻道並開始播放音樂。

再測試 YouTube 網址：
```
/play https://www.youtube.com/watch?v=dQw4w9WgXcQ
```
預期：Bot 播放該影片的音訊。

---

### Task 5: 新增 /skip、/stop、/queue

**Files:**
- Modify: `discord-bot/music.py`

- [ ] **Step 1: 在 MusicCog 內，play_next 方法之後加入以下三個指令**

```python
    @app_commands.command(name='skip', description='跳過目前這首歌')
    async def skip(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await interaction.response.send_message('⏭️ 已跳過！')
        else:
            await interaction.response.send_message('❌ 目前沒有正在播放的歌曲。', ephemeral=True)

    @app_commands.command(name='stop', description='停止播放並離開語音頻道')
    async def stop(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc:
            self.queues.pop(interaction.guild_id, None)
            self.now_playing.pop(interaction.guild_id, None)
            await vc.disconnect()
            await interaction.response.send_message('⏹️ 已停止播放並離開語音頻道。')
        else:
            await interaction.response.send_message('❌ Bot 目前不在語音頻道。', ephemeral=True)

    @app_commands.command(name='queue', description='顯示目前播放清單')
    async def queue_cmd(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild_id)
        now = self.now_playing.get(interaction.guild_id)
        if not now and not queue:
            await interaction.response.send_message('📭 播放清單是空的。')
            return
        embed = discord.Embed(title='🎵 播放清單', color=discord.Color.green())
        if now:
            embed.add_field(name='▶️ 正在播放', value=now[0], inline=False)
        for i, (title, _) in enumerate(queue, 1):
            embed.add_field(name=f'{i}.', value=title, inline=False)
        await interaction.response.send_message(embed=embed)
```

- [ ] **Step 2: 測試**

啟動 Bot，播放一首歌後：
- 輸入 `/skip` → 預期：顯示「已跳過」
- 輸入 `/stop` → 預期：Bot 離開語音頻道
- 播放兩首歌後輸入 `/queue` → 預期：顯示清單 embed

---

### Task 6: 新增 /pause、/resume、/nowplaying

**Files:**
- Modify: `discord-bot/music.py`

- [ ] **Step 1: 在 queue_cmd 方法之後加入以下三個指令**

```python
    @app_commands.command(name='pause', description='暫停播放')
    async def pause(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message('⏸️ 已暫停。')
        else:
            await interaction.response.send_message('❌ 目前沒有正在播放的歌曲。', ephemeral=True)

    @app_commands.command(name='resume', description='繼續播放')
    async def resume(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message('▶️ 繼續播放！')
        else:
            await interaction.response.send_message('❌ 目前沒有暫停的歌曲。', ephemeral=True)

    @app_commands.command(name='nowplaying', description='顯示目前播放的歌曲')
    async def nowplaying(self, interaction: discord.Interaction):
        now = self.now_playing.get(interaction.guild_id)
        if now:
            await interaction.response.send_message(f'🎵 正在播放：**{now[0]}**')
        else:
            await interaction.response.send_message('❌ 目前沒有正在播放的歌曲。', ephemeral=True)
```

- [ ] **Step 2: 測試全部指令**

啟動 Bot，逐一測試：
- `/play 歌名` → 播放
- `/pause` → 暫停
- `/resume` → 繼續
- `/nowplaying` → 顯示歌曲名稱
- `/play 另一首` → 加入佇列
- `/queue` → 顯示清單
- `/skip` → 跳下一首
- `/stop` → 離開頻道
- `/help` → 顯示說明

---

### Task 7: 部署到 Discloud

**Files:** 無新增（打包現有檔案）

- [ ] **Step 1: 確認 Discloud 帳號**

前往 https://discloud.com，用 Discord 帳號登入。

- [ ] **Step 2: 打包上傳檔案**

將以下檔案打包成 `bot.zip`（**不要**包含 `.env` 和 `CIFAR10/` 等資料夾）：
```
main.py
music.py
requirements.txt
discloud.config
```

- [ ] **Step 3: 在 Discloud 設定環境變數**

上傳後，在 Discloud 控制台找到你的 Bot → 「Config」→ 新增以下環境變數：
```
DISCORD_TOKEN = 你的Token
SPOTIFY_CLIENT_ID = 你的ID
SPOTIFY_CLIENT_SECRET = 你的Secret
```

- [ ] **Step 4: 啟動並確認上線**

Discloud 控制台點「Start」，Bot 狀態變為綠色 Online。
回到 Discord 伺服器，輸入 `/help` 確認 Bot 正常回應。
