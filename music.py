import os
import asyncio
import subprocess
import random
import logging
import re
import sys

import discord
from discord.ext import commands
from discord import app_commands
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from collections import deque, namedtuple

log = logging.getLogger('music')

FFMPEG_EXECUTABLE = os.getenv(
    'FFMPEG_PATH',
    r'C:\ffmpeg\bin\ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg',
)
YTDLP_EXECUTABLE = os.getenv(
    'YTDLP_PATH',
    'yt-dlp' if sys.platform == 'win32' else '/home/ubuntu/miniconda3/bin/yt-dlp',
)

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

TrackInfo = namedtuple('TrackInfo', ['title', 'url', 'duration', 'thumbnail', 'webpage_url'])


class MusicControls(discord.ui.View):
    def __init__(self, cog: 'MusicCog', guild: discord.Guild):
        super().__init__(timeout=3600)
        self.cog = cog
        self.guild = guild

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label='⏸️ 暫停', style=discord.ButtonStyle.secondary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            button.label = '▶️ 繼續'
            button.style = discord.ButtonStyle.success
            await interaction.response.edit_message(view=self)
        elif vc and vc.is_paused():
            vc.resume()
            button.label = '⏸️ 暫停'
            button.style = discord.ButtonStyle.secondary
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.send_message('❌ 沒有正在播放的歌曲', ephemeral=True)

    @discord.ui.button(label='⏭️ 跳過', style=discord.ButtonStyle.primary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message('⏭️ 已跳過！', ephemeral=True)
        else:
            await interaction.response.send_message('❌ 沒有正在播放的歌曲', ephemeral=True)

    @discord.ui.button(label='🔁 循環', style=discord.ButtonStyle.secondary)
    async def loop_toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = self.guild.id
        self.cog.loop_mode[guild_id] = not self.cog.loop_mode.get(guild_id, False)
        is_loop = self.cog.loop_mode[guild_id]
        button.style = discord.ButtonStyle.success if is_loop else discord.ButtonStyle.secondary
        status = '開啟' if is_loop else '關閉'
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f'🔁 循環播放已{status}', ephemeral=True)

    @discord.ui.button(label='🔀 洗牌', style=discord.ButtonStyle.secondary)
    async def shuffle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        queue = self.cog.get_queue(self.guild.id)
        if not queue:
            await interaction.response.send_message('📭 播放清單是空的', ephemeral=True)
            return
        lst = list(queue)
        random.shuffle(lst)
        self.cog.queues[self.guild.id] = deque(lst)
        await interaction.response.send_message(f'🔀 已隨機排列 {len(lst)} 首歌曲！', ephemeral=True)

    @discord.ui.button(label='⏹️ 停止', style=discord.ButtonStyle.danger)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if vc:
            self.cog._cleanup_guild(self.guild.id)
            await vc.disconnect()
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
            await interaction.followup.send('⏹️ 已停止播放並離開語音頻道。', ephemeral=True)
        else:
            await interaction.response.send_message('❌ Bot 不在語音頻道', ephemeral=True)


class MusicCog(commands.Cog):
    _SPOTIFY_TRACK_RE = re.compile(r'open\.spotify\.com/track/(\w+)')
    _SPOTIFY_COLLECTION_RE = re.compile(r'open\.spotify\.com/(album|playlist|artist)/')
    _YT_PLAYLIST_RE = re.compile(r'(?:youtube\.com|youtu\.be).*[?&]list=')

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queues: dict[int, deque] = {}
        self.now_playing: dict[int, TrackInfo] = {}
        self.loop_mode: dict[int, bool] = {}
        self._play_locks: dict[int, asyncio.Lock] = {}
        self.autoplay: dict[int, bool] = {}
        self._play_history: dict[int, deque[str]] = {}
        self._text_channels: dict[int, discord.abc.Messageable] = {}
        self.sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=os.getenv('SPOTIFY_CLIENT_ID'),
            client_secret=os.getenv('SPOTIFY_CLIENT_SECRET'),
        ))

    def get_queue(self, guild_id: int) -> deque:
        if guild_id not in self.queues:
            self.queues[guild_id] = deque()
        return self.queues[guild_id]

    def _get_play_lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self._play_locks:
            self._play_locks[guild_id] = asyncio.Lock()
        return self._play_locks[guild_id]

    def _cleanup_guild(self, guild_id: int):
        """Clean up playback state. Preserves user preferences (autoplay)."""
        self.queues.pop(guild_id, None)
        self.now_playing.pop(guild_id, None)
        self.loop_mode.pop(guild_id, None)
        self._play_history.pop(guild_id, None)
        self._text_channels.pop(guild_id, None)

    def _add_to_history(self, guild_id: int, track: TrackInfo):
        if guild_id not in self._play_history:
            self._play_history[guild_id] = deque(maxlen=50)
        vid = track.webpage_url.split('v=')[-1].split('&')[0]
        if vid not in self._play_history[guild_id]:
            self._play_history[guild_id].append(vid)

    # ── Spotify helpers ───────────────────────────────────────────────

    def _resolve_spotify(self, query: str) -> tuple[str | None, str | None]:
        """Returns (resolved_query, error_message). One will be None."""
        if self._SPOTIFY_COLLECTION_RE.search(query):
            return None, '❌ 目前只支援 Spotify 單曲連結，不支援 album / playlist / artist。'
        m = self._SPOTIFY_TRACK_RE.search(query)
        if m:
            try:
                track = self.sp.track(m.group(1))
                return f"{track['name']} {track['artists'][0]['name']}", None
            except Exception:
                log.exception('Spotify API error for track %s', m.group(1))
                return None, '❌ 無法從 Spotify 取得歌曲資訊，請稍後再試。'
        return query, None

    # ── yt-dlp helpers ────────────────────────────────────────────────

    def _run_ytdlp(self, args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
        return subprocess.run(
            [YTDLP_EXECUTABLE] + args,
            capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
            env=env,
        )

    @staticmethod
    def _decode(raw: bytes) -> str:
        return raw.decode('utf-8', errors='replace')

    def fetch_audio(self, query: str) -> TrackInfo:
        """Fetch metadata + stream URL. Used for the first song (immediate play)."""
        if not query.startswith('http'):
            query = f'ytsearch1:{query}'

        result = self._run_ytdlp([
            '-f', 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best',
            '--no-playlist', '--no-warnings',
            '--extractor-args', 'youtube:player_client=android_vr',
            '--print', '%(title)s',
            '--print', '%(id)s',
            '--print', '%(duration_string)s',
            '-g', query,
        ])

        stdout = self._decode(result.stdout)
        lines = [l for l in stdout.strip().split('\n') if l]

        if len(lines) < 4:
            stderr = self._decode(result.stderr)
            if stderr:
                log.error('yt-dlp stderr:\n%s', stderr[-500:])
            raise RuntimeError(f'yt-dlp returned {len(lines)} lines, expected >= 4')

        title = lines[0]
        video_id = lines[1]
        duration = lines[2]
        url = lines[3]

        return TrackInfo(
            title=title,
            url=url,
            duration=duration,
            thumbnail=f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg',
            webpage_url=f'https://www.youtube.com/watch?v={video_id}',
        )

    def fetch_stream_url(self, webpage_url: str) -> str:
        """Re-resolve a fresh stream URL right before playback (avoids expiry)."""
        result = self._run_ytdlp([
            '-f', 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best',
            '--no-playlist', '--no-warnings',
            '--extractor-args', 'youtube:player_client=android_vr',
            '-g', webpage_url,
        ])

        stdout = self._decode(result.stdout)
        lines = [l for l in stdout.strip().split('\n') if l]

        if not lines:
            stderr = self._decode(result.stderr)
            if stderr:
                log.error('yt-dlp re-resolve stderr:\n%s', stderr[-500:])
            raise RuntimeError('Failed to re-resolve stream URL')

        return lines[0]

    def fetch_playlist(self, url: str) -> list[TrackInfo]:
        """Fetch metadata for all tracks in a YouTube playlist (no stream URLs)."""
        result = self._run_ytdlp([
            '--flat-playlist', '--yes-playlist', '--no-warnings',
            '--print', '%(title)s|||%(id)s|||%(duration_string)s',
            url,
        ], timeout=120)

        stdout = self._decode(result.stdout)
        lines = [l for l in stdout.strip().split('\n') if l]

        if not lines:
            stderr = self._decode(result.stderr)
            if stderr:
                log.error('yt-dlp playlist stderr:\n%s', stderr[-500:])
            raise RuntimeError('無法取得播放清單資訊')

        tracks = []
        for line in lines:
            parts = line.split('|||')
            if len(parts) < 3:
                continue
            title, video_id, duration = parts[0], parts[1], parts[2]
            if not video_id or video_id == 'NA':
                continue
            if not duration or duration == 'NA':
                duration = '?:??'
            tracks.append(TrackInfo(
                title=title if title and title != 'NA' else f'(影片 {video_id})',
                url='',
                duration=duration,
                thumbnail=f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg',
                webpage_url=f'https://www.youtube.com/watch?v={video_id}',
            ))

        return tracks

    def _fetch_autoplay_track(self, guild_id: int, last_track: TrackInfo) -> TrackInfo | None:
        """Get one recommended track via YouTube Radio mix (RD{video_id})."""
        video_id = last_track.webpage_url.split('v=')[-1].split('&')[0]
        mix_url = f'https://www.youtube.com/watch?v={video_id}&list=RD{video_id}'

        result = self._run_ytdlp([
            '--flat-playlist', '--yes-playlist', '--no-warnings',
            '--playlist-start', '2',
            '--playlist-end', '26',
            '--print', '%(title)s|||%(id)s|||%(duration_string)s',
            mix_url,
        ], timeout=30)

        stdout = self._decode(result.stdout)
        lines = [l for l in stdout.strip().split('\n') if l]
        history = set(self._play_history.get(guild_id, []))

        candidates = []
        for line in lines:
            parts = line.split('|||')
            if len(parts) < 3:
                continue
            title, vid, duration = parts[0], parts[1], parts[2]
            if not vid or vid == 'NA':
                continue
            if not duration or duration == 'NA':
                duration = '?:??'
            track = TrackInfo(
                title=title if title and title != 'NA' else f'(影片 {vid})',
                url='',
                duration=duration,
                thumbnail=f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg',
                webpage_url=f'https://www.youtube.com/watch?v={vid}',
            )
            if vid not in history:
                return track
            candidates.append(track)

        return random.choice(candidates) if candidates else None

    # ── Embed builder ─────────────────────────────────────────────────

    def make_embed(self, track: TrackInfo, status: str = '▶️ 正在播放',
                   color: discord.Color = discord.Color.blurple()) -> discord.Embed:
        embed = discord.Embed(
            title=status,
            description=f'**[{track.title}]({track.webpage_url})**',
            color=color,
        )
        embed.set_thumbnail(url=track.thumbnail)
        embed.add_field(name='時長', value=track.duration, inline=True)
        return embed

    # ── Playback engine ───────────────────────────────────────────────

    def play_next(self, guild: discord.Guild, error=None):
        """Called from ffmpeg's after callback (worker thread). Schedules async work."""
        if error:
            log.error('Playback error in guild %s: %s', guild.id, error)
        asyncio.run_coroutine_threadsafe(self._play_next_async(guild), self.bot.loop)

    async def _play_next_async(self, guild: discord.Guild):
        try:
            vc = guild.voice_client
            if not vc:
                return

            loop = asyncio.get_running_loop()

            # Loop mode: re-resolve and replay current track
            if self.loop_mode.get(guild.id) and self.now_playing.get(guild.id):
                track = self.now_playing[guild.id]
                try:
                    url = await loop.run_in_executor(
                        None, self.fetch_stream_url, track.webpage_url)
                except Exception:
                    log.exception('Loop re-resolve failed: %s', track.title)
                    # Fall through to try next from queue instead
                else:
                    source = discord.FFmpegPCMAudio(
                        url, executable=FFMPEG_EXECUTABLE, **FFMPEG_OPTIONS)
                    try:
                        vc.play(source, after=lambda e: self.play_next(guild, e))
                    except discord.ClientException:
                        log.warning('Already playing (loop), guild %s', guild.id)
                    return

            queue = self.get_queue(guild.id)

            # Autoplay: inject a recommendation when queue is empty
            if not queue and self.autoplay.get(guild.id) and self.now_playing.get(guild.id):
                last = self.now_playing[guild.id]
                try:
                    rec = await loop.run_in_executor(
                        None, self._fetch_autoplay_track, guild.id, last)
                except Exception:
                    log.exception('Autoplay fetch failed, guild %s', guild.id)
                    rec = None
                if rec:
                    queue.append(rec)
                    channel = self._text_channels.get(guild.id)
                    if channel:
                        try:
                            await channel.send(
                                embed=self.make_embed(rec, status='🎶 自動推薦播放'))
                        except Exception:
                            pass

            # Normal: play next from queue
            if queue:
                track = queue.popleft()
                self.now_playing[guild.id] = track
                self._add_to_history(guild.id, track)
                try:
                    url = await loop.run_in_executor(
                        None, self.fetch_stream_url, track.webpage_url)
                except Exception:
                    log.exception('Resolve failed, skipping: %s', track.title)
                    self.play_next(guild)
                    return
                source = discord.FFmpegPCMAudio(
                    url, executable=FFMPEG_EXECUTABLE, **FFMPEG_OPTIONS)
                try:
                    vc.play(source, after=lambda e: self.play_next(guild, e))
                except discord.ClientException:
                    log.warning('Already playing (next), guild %s', guild.id)
            else:
                self._cleanup_guild(guild.id)
                await vc.disconnect()

        except Exception:
            log.exception('Unhandled error in _play_next_async, guild %s', guild.id)

    # ── /play ─────────────────────────────────────────────────────────
    @app_commands.command(name='play', description='播放音樂（YouTube / Spotify 網址或歌名）')
    @app_commands.describe(query='歌名、YouTube 網址或 Spotify 網址')
    async def play(self, interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            await interaction.response.send_message('❌ 請先加入語音頻道！', ephemeral=True)
            return

        await interaction.response.defer()

        try:
            self._text_channels[interaction.guild_id] = interaction.channel

            resolved, error = self._resolve_spotify(query)
            if error:
                await interaction.followup.send(error)
                return
            query = resolved

            vc = interaction.guild.voice_client
            if vc is None:
                vc = await interaction.user.voice.channel.connect()
            elif vc.channel != interaction.user.voice.channel:
                await vc.move_to(interaction.user.voice.channel)

            # ── YouTube playlist ───────────────────────────────────
            if self._YT_PLAYLIST_RE.search(query):
                try:
                    tracks = await asyncio.get_running_loop().run_in_executor(
                        None, self.fetch_playlist, query)
                except subprocess.TimeoutExpired:
                    await interaction.followup.send('❌ 播放清單載入逾時，請稍後再試。')
                    return
                except Exception:
                    log.exception('fetch_playlist failed: %s', query)
                    await interaction.followup.send(
                        '❌ 無法載入播放清單，請確認連結是否正確。')
                    return

                if not tracks:
                    await interaction.followup.send('❌ 播放清單是空的或無法讀取。')
                    return

                async with self._get_play_lock(interaction.guild_id):
                    queue = self.get_queue(interaction.guild_id)
                    if vc.is_playing() or vc.is_paused():
                        queue.extend(tracks)
                        embed = discord.Embed(
                            title='✅ 已加入播放清單',
                            description=f'新增了 **{len(tracks)}** 首歌曲到佇列',
                            color=discord.Color.green(),
                        )
                        embed.add_field(name='佇列總數',
                                        value=f'{len(queue)} 首', inline=True)
                        await interaction.followup.send(embed=embed)
                    else:
                        first = tracks[0]
                        rest = tracks[1:]
                        try:
                            url = await asyncio.get_running_loop().run_in_executor(
                                None, self.fetch_stream_url, first.webpage_url)
                        except Exception:
                            log.exception('Resolve first playlist track failed: %s',
                                          first.title)
                            await interaction.followup.send(
                                '❌ 無法播放清單中的第一首歌曲。')
                            return
                        self.now_playing[interaction.guild_id] = first
                        self._add_to_history(interaction.guild_id, first)
                        source = discord.FFmpegPCMAudio(
                            url, executable=FFMPEG_EXECUTABLE, **FFMPEG_OPTIONS)
                        vc.play(source, after=lambda e: self.play_next(
                            interaction.guild, e))
                        queue.extend(rest)
                        view = MusicControls(self, interaction.guild)
                        embed = self.make_embed(first)
                        if rest:
                            embed.add_field(
                                name='📋 播放清單',
                                value=f'另有 {len(rest)} 首已加入佇列',
                                inline=True)
                        msg = await interaction.followup.send(
                            embed=embed, view=view)
                        view.message = msg
                return

            # ── Single track ──────────────────────────────────────
            try:
                track = await asyncio.get_running_loop().run_in_executor(
                    None, self.fetch_audio, query)
            except subprocess.TimeoutExpired:
                await interaction.followup.send('❌ 搜尋逾時，請稍後再試。')
                return
            except Exception:
                log.exception('fetch_audio failed: %s', query)
                await interaction.followup.send(
                    '❌ 找不到該歌曲，請換個關鍵字或確認連結是否正確。')
                return

            async with self._get_play_lock(interaction.guild_id):
                queue = self.get_queue(interaction.guild_id)
                if vc.is_playing() or vc.is_paused():
                    queue.append(track)
                    embed = self.make_embed(track, status='✅ 已加入佇列',
                                            color=discord.Color.green())
                    embed.add_field(name='佇列位置', value=f'#{len(queue)}', inline=True)
                    await interaction.followup.send(embed=embed)
                else:
                    self.now_playing[interaction.guild_id] = track
                    self._add_to_history(interaction.guild_id, track)
                    source = discord.FFmpegPCMAudio(
                        track.url, executable=FFMPEG_EXECUTABLE, **FFMPEG_OPTIONS)
                    vc.play(source, after=lambda e: self.play_next(interaction.guild, e))
                    view = MusicControls(self, interaction.guild)
                    msg = await interaction.followup.send(
                        embed=self.make_embed(track), view=view)
                    view.message = msg

        except Exception as e:
            log.exception('/play error')
            try:
                await interaction.followup.send(f'❌ 發生錯誤：{e}')
            except Exception:
                pass

    # ── /skip ─────────────────────────────────────────────────────────
    @app_commands.command(name='skip', description='跳過目前這首歌')
    async def skip(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message('⏭️ 已跳過！')
        else:
            await interaction.response.send_message(
                '❌ 目前沒有正在播放的歌曲。', ephemeral=True)

    # ── /stop ─────────────────────────────────────────────────────────
    @app_commands.command(name='stop', description='停止播放並離開語音頻道')
    async def stop(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc:
            self._cleanup_guild(interaction.guild_id)
            await vc.disconnect()
            await interaction.response.send_message('⏹️ 已停止播放並離開語音頻道。')
        else:
            await interaction.response.send_message(
                '❌ Bot 目前不在語音頻道。', ephemeral=True)

    # ── /queue ────────────────────────────────────────────────────────
    @app_commands.command(name='queue', description='顯示目前播放清單')
    @app_commands.describe(page='頁碼（每頁 10 首）')
    async def queue_cmd(self, interaction: discord.Interaction, page: int = 1):
        queue = list(self.get_queue(interaction.guild_id))
        now = self.now_playing.get(interaction.guild_id)
        if not now and not queue:
            await interaction.response.send_message('📭 播放清單是空的。')
            return

        per_page = 10
        total_pages = max(1, (len(queue) + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        start = (page - 1) * per_page

        embed = discord.Embed(
            title=f'🎵 播放清單（第 {page}/{total_pages} 頁）',
            color=discord.Color.green(),
        )

        loop_tag = ' 🔁' if self.loop_mode.get(interaction.guild_id) else ''
        if now:
            embed.add_field(
                name=f'▶️ 正在播放{loop_tag}',
                value=f'**{now.title}** `{now.duration}`',
                inline=False,
            )
            embed.set_thumbnail(url=now.thumbnail)

        if queue:
            lines = []
            total_len = 0
            for i, t in enumerate(queue[start:start + per_page]):
                title = t.title if len(t.title) <= 60 else t.title[:57] + '…'
                line = f'`{start + i + 1}.` **{title}** `{t.duration}`'
                if total_len + len(line) + 1 > 1000:
                    remaining = per_page - i
                    lines.append(f'… 還有 {remaining} 首')
                    break
                lines.append(line)
                total_len += len(line) + 1
            embed.add_field(name='待播清單', value='\n'.join(lines), inline=False)

        embed.set_footer(text=f'共 {len(queue)} 首待播　輸入 /queue <頁碼> 翻頁　用 /remove <編號> 刪歌')
        await interaction.response.send_message(embed=embed)

    # ── /remove ───────────────────────────────────────────────────────
    @app_commands.command(name='remove', description='從播放清單移除指定位置的歌曲')
    @app_commands.describe(position='要移除的歌曲位置（從 1 開始，對應 /queue 顯示的編號）')
    async def remove(self, interaction: discord.Interaction, position: int):
        queue = self.get_queue(interaction.guild_id)
        if not queue:
            await interaction.response.send_message('📭 播放清單是空的。', ephemeral=True)
            return
        if position < 1 or position > len(queue):
            await interaction.response.send_message(
                f'❌ 位置無效，請輸入 1 到 {len(queue)} 之間的數字。', ephemeral=True)
            return
        lst = list(queue)
        removed = lst.pop(position - 1)
        self.queues[interaction.guild_id] = deque(lst)
        await interaction.response.send_message(
            f'🗑️ 已移除第 {position} 首：**{removed.title}**')

    # ── /pause ────────────────────────────────────────────────────────
    @app_commands.command(name='pause', description='暫停播放')
    async def pause(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message('⏸️ 已暫停。')
        else:
            await interaction.response.send_message(
                '❌ 目前沒有正在播放的歌曲。', ephemeral=True)

    # ── /resume ───────────────────────────────────────────────────────
    @app_commands.command(name='resume', description='繼續播放')
    async def resume(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message('▶️ 繼續播放！')
        else:
            await interaction.response.send_message(
                '❌ 目前沒有暫停的歌曲。', ephemeral=True)

    # ── /nowplaying ───────────────────────────────────────────────────
    @app_commands.command(name='nowplaying', description='顯示目前播放的歌曲')
    async def nowplaying(self, interaction: discord.Interaction):
        now = self.now_playing.get(interaction.guild_id)
        if now:
            view = MusicControls(self, interaction.guild)
            await interaction.response.send_message(embed=self.make_embed(now), view=view)
            view.message = await interaction.original_response()
        else:
            await interaction.response.send_message(
                '❌ 目前沒有正在播放的歌曲。', ephemeral=True)

    # ── /loop ─────────────────────────────────────────────────────────
    @app_commands.command(name='loop', description='切換循環播放模式')
    async def loop_cmd(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        self.loop_mode[guild_id] = not self.loop_mode.get(guild_id, False)
        status = '開啟 🔁' if self.loop_mode[guild_id] else '關閉'
        await interaction.response.send_message(f'循環播放已{status}')

    # ── /shuffle ──────────────────────────────────────────────────────
    @app_commands.command(name='shuffle', description='隨機洗牌播放清單')
    async def shuffle(self, interaction: discord.Interaction):
        queue = self.get_queue(interaction.guild_id)
        if not queue:
            await interaction.response.send_message(
                '📭 播放清單是空的。', ephemeral=True)
            return
        lst = list(queue)
        random.shuffle(lst)
        self.queues[interaction.guild_id] = deque(lst)
        await interaction.response.send_message(f'🔀 已隨機排列 {len(lst)} 首歌曲！')

    # ── /autoplay ──────────────────────────────────────────────────────
    @app_commands.command(name='autoplay', description='切換自動推薦播放（佇列結束後自動播放相關歌曲）')
    async def autoplay_cmd(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        self.autoplay[guild_id] = not self.autoplay.get(guild_id, False)
        status = '開啟 🎶' if self.autoplay[guild_id] else '關閉'
        await interaction.response.send_message(f'自動推薦播放已{status}')

    # ── Auto-disconnect when channel is empty ─────────────────────────
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member,
                                    before: discord.VoiceState,
                                    after: discord.VoiceState):
        vc = member.guild.voice_client
        if not vc:
            return
        if len(vc.channel.members) == 1:
            self._cleanup_guild(member.guild.id)
            await vc.disconnect()


async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCog(bot))
