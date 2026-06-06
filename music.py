import os
import asyncio
import random
import logging
import re
import sys

import discord
from discord.ext import commands
from discord import app_commands
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import yt_dlp
from collections import deque, namedtuple

log = logging.getLogger('music')

FFMPEG_EXECUTABLE = os.getenv(
    'FFMPEG_PATH',
    r'C:\ffmpeg\bin\ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg',
)

FFMPEG_OPTIONS = {
    # Robust reconnection for flaky networks. -reconnect_on_network_error
    # recovers from the transient "Input/output error" stream drops.
    'before_options': (
        '-reconnect 1 -reconnect_streamed 1 '
        '-reconnect_on_network_error 1 -reconnect_delay_max 10'
    ),
    # discord.py hardcodes "-loglevel warning" right before the output options,
    # so our -loglevel must live here (after it) to actually win and silence
    # the noisy "Will reconnect" / TLS pull-error chatter.
    'options': '-vn -loglevel fatal',
}

# Shared yt-dlp options. Using the Python API avoids per-call process spawn
# overhead and all the stdout encoding hacks the subprocess version needed.
YDL_BASE_OPTS = {
    'format': 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best',
    'quiet': True,
    'no_warnings': True,
    'noplaylist': True,
    'socket_timeout': 30,
    'extractor_args': {'youtube': {'player_client': ['android_vr']}},
}

TrackInfo = namedtuple('TrackInfo', ['title', 'url', 'duration', 'thumbnail', 'webpage_url', 'playlist_tag'])
TrackInfo.__new__.__defaults__ = (None,)  # playlist_tag defaults to None


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
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
            await interaction.followup.send('⏹️ 已停止播放並離開語音頻道。', ephemeral=True)
            await self.cog._safe_disconnect(vc)
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
        self._playlist_counter: dict[int, int] = {}
        # Gapless playback: pre-resolved stream URL for the next queued track
        self._prefetch: dict[int, tuple[str, str]] = {}      # gid -> (webpage_url, stream_url)
        self._prefetch_tasks: dict[int, asyncio.Task] = {}
        self._leave_tasks: dict[int, asyncio.Task] = {}      # empty-channel grace timers
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
        self._playlist_counter.pop(guild_id, None)
        self._play_locks.pop(guild_id, None)
        self._prefetch.pop(guild_id, None)
        task = self._prefetch_tasks.pop(guild_id, None)
        if task and not task.done():
            task.cancel()

    def _next_playlist_tag(self, guild_id: int) -> int:
        self._playlist_counter[guild_id] = self._playlist_counter.get(guild_id, 0) + 1
        return self._playlist_counter[guild_id]

    def _add_to_history(self, guild_id: int, track: TrackInfo):
        if guild_id not in self._play_history:
            self._play_history[guild_id] = deque(maxlen=50)
        vid = self._video_id(track.webpage_url)
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

    # ── yt-dlp helpers (Python API) ───────────────────────────────────

    @staticmethod
    def _fmt_duration(seconds) -> str:
        """Format duration seconds → 'M:SS' or 'H:MM:SS'."""
        if not seconds:
            return '?:??'
        seconds = int(seconds)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'

    @staticmethod
    def _video_id(webpage_url: str) -> str:
        """Extract the YouTube video id from a watch URL."""
        return webpage_url.split('v=')[-1].split('&')[0]

    def _info_to_track(self, info: dict) -> TrackInfo:
        """Build a TrackInfo from a yt-dlp info dict (flat or full)."""
        vid = info.get('id') or ''
        title = info.get('title') or f'(影片 {vid})'
        return TrackInfo(
            title=title,
            url=info.get('url', ''),
            duration=self._fmt_duration(info.get('duration')),
            thumbnail=f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg',
            webpage_url=f'https://www.youtube.com/watch?v={vid}',
        )

    def _extract(self, query: str, *, flat: bool = False,
                 playlist: bool = False, playlistend: int | None = None) -> dict:
        """Run a yt-dlp extraction in-process. Caller runs this in an executor."""
        opts = dict(YDL_BASE_OPTS)
        if flat:
            opts['extract_flat'] = True
        if playlist:
            opts['noplaylist'] = False
        if playlistend is not None:
            opts['playlistend'] = playlistend
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(query, download=False)

    def fetch_audio(self, query: str) -> TrackInfo:
        """Resolve metadata + stream URL for a single track (immediate play)."""
        if not query.startswith('http'):
            query = f'ytsearch1:{query}'
        info = self._extract(query)
        if 'entries' in info:
            entries = [e for e in info['entries'] if e]
            if not entries:
                raise RuntimeError('No search results')
            info = entries[0]
        track = self._info_to_track(info)
        if not track.url:
            raise RuntimeError('No playable stream URL in result')
        return track

    def fetch_stream_url(self, webpage_url: str) -> str:
        """Re-resolve a fresh stream URL right before playback (avoids expiry)."""
        info = self._extract(webpage_url)
        if 'entries' in info:
            info = info['entries'][0]
        url = info.get('url')
        if not url:
            raise RuntimeError('Failed to re-resolve stream URL')
        return url

    def fetch_playlist(self, url: str) -> list[TrackInfo]:
        """Fetch metadata for all tracks in a YouTube playlist (no stream URLs)."""
        info = self._extract(url, flat=True, playlist=True)
        entries = [e for e in info.get('entries', []) if e and e.get('id')]
        if not entries:
            raise RuntimeError('無法取得播放清單資訊')
        return [self._info_to_track(e) for e in entries]

    def _pick_track_from_entries(self, entries: list[dict], history: set[str],
                                 exclude_id: str = '') -> TrackInfo | None:
        """Pick the first entry not already in history (else a random one)."""
        candidates = []
        for e in entries:
            if not e:
                continue
            vid = e.get('id')
            if not vid or vid == exclude_id:
                continue
            track = self._info_to_track(e)
            if vid not in history:
                return track
            candidates.append(track)
        return random.choice(candidates) if candidates else None

    def _fetch_autoplay_track(self, guild_id: int, last_track: TrackInfo) -> TrackInfo | None:
        """Get one recommended track. Tries YouTube Radio Mix first, then search."""
        video_id = self._video_id(last_track.webpage_url)
        history = set(self._play_history.get(guild_id, []))

        # Strategy 1: YouTube Radio Mix (RD{video_id})
        mix_url = f'https://www.youtube.com/watch?v={video_id}&list=RD{video_id}'
        log.info('Autoplay: trying Radio Mix for %s', video_id)
        try:
            info = self._extract(mix_url, flat=True, playlist=True, playlistend=25)
            track = self._pick_track_from_entries(
                info.get('entries', []), history, exclude_id=video_id)
            if track:
                log.info('Autoplay: picked "%s" from Radio Mix', track.title)
                return track
        except Exception as e:
            log.warning('Autoplay: Radio Mix failed: %s', e)

        # Strategy 2: YouTube search fallback
        log.info('Autoplay: falling back to search "%s"', last_track.title)
        try:
            info = self._extract(f'ytsearch5:{last_track.title}', flat=True)
            track = self._pick_track_from_entries(
                info.get('entries', []), history, exclude_id=video_id)
            if track:
                log.info('Autoplay: picked "%s" from search', track.title)
                return track
        except Exception as e:
            log.warning('Autoplay: search failed: %s', e)

        log.warning('Autoplay: no track found for "%s"', last_track.title)
        return None

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

    @staticmethod
    async def _ensure_stopped(vc: discord.VoiceClient):
        """Stop any lingering playback so vc.play() won't raise ClientException."""
        if vc.is_playing() or vc.is_paused():
            vc.stop()
            await asyncio.sleep(0.3)

    async def _safe_disconnect(self, vc: discord.VoiceClient):
        """Disconnect without letting a slow/timed-out voice handshake raise.

        On flaky networks the disconnect confirmation can time out; older
        discord.py versions let that TimeoutError propagate and break the
        caller. force=True + swallowing the error keeps us robust."""
        try:
            await vc.disconnect(force=True)
        except Exception as e:
            log.warning('Voice disconnect error (ignored): %s', e)

    # ── Gapless prefetch ───────────────────────────────────────────────

    def _schedule_prefetch(self, guild_id: int):
        """Resolve the next queued track's stream URL in the background so the
        transition between songs has no audible gap. Safe to call repeatedly."""
        queue = self.queues.get(guild_id)
        if not queue:
            return
        next_url = queue[0].webpage_url
        cached = self._prefetch.get(guild_id)
        if cached and cached[0] == next_url:
            return  # already prefetched this track
        old = self._prefetch_tasks.get(guild_id)
        if old and not old.done():
            old.cancel()
        self._prefetch_tasks[guild_id] = self.bot.loop.create_task(
            self._prefetch_worker(guild_id, next_url))

    async def _prefetch_worker(self, guild_id: int, webpage_url: str):
        try:
            loop = asyncio.get_running_loop()
            url = await loop.run_in_executor(None, self.fetch_stream_url, webpage_url)
            self._prefetch[guild_id] = (webpage_url, url)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug('Prefetch failed for %s: %s', webpage_url, e)

    async def _resolve_stream(self, guild_id: int, track: TrackInfo) -> str:
        """Return a stream URL for track, using the prefetched one if it matches."""
        cached = self._prefetch.pop(guild_id, None)
        if cached and cached[0] == track.webpage_url:
            return cached[1]
        return await asyncio.get_running_loop().run_in_executor(
            None, self.fetch_stream_url, track.webpage_url)

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
                    await self._ensure_stopped(vc)
                    source = discord.FFmpegPCMAudio(
                        url, executable=FFMPEG_EXECUTABLE, **FFMPEG_OPTIONS)
                    vc.play(source, after=lambda e: self.play_next(guild, e))
                    return

            queue = self.get_queue(guild.id)

            # Autoplay: inject a recommendation when queue is empty
            if not queue and self.autoplay.get(guild.id, True) and self.now_playing.get(guild.id):
                last = self.now_playing[guild.id]
                log.info('Autoplay triggered for guild %s, last: %s', guild.id, last.title)
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
                            view = MusicControls(self, guild)
                            msg = await channel.send(
                                embed=self.make_embed(rec), view=view)
                            view.message = msg
                        except Exception:
                            pass
                else:
                    log.warning('Autoplay: no recommendation found, will disconnect')

            # Normal: play next from queue
            if queue:
                track = queue.popleft()
                self.now_playing[guild.id] = track
                self._add_to_history(guild.id, track)
                try:
                    url = await self._resolve_stream(guild.id, track)
                except Exception:
                    log.exception('Resolve failed, skipping: %s', track.title)
                    self.play_next(guild)
                    return
                await self._ensure_stopped(vc)
                source = discord.FFmpegPCMAudio(
                    url, executable=FFMPEG_EXECUTABLE, **FFMPEG_OPTIONS)
                vc.play(source, after=lambda e: self.play_next(guild, e))
                self._schedule_prefetch(guild.id)  # warm up the following track
            else:
                self._cleanup_guild(guild.id)
                await self._safe_disconnect(vc)

        except Exception:
            log.exception('Unhandled error in _play_next_async, guild %s', guild.id)

    # ── /play & /playnext shared logic ───────────────────────────────
    async def _play_impl(self, interaction: discord.Interaction, query: str,
                         insert_next: bool = False):
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
                except Exception:
                    log.exception('fetch_playlist failed: %s', query)
                    await interaction.followup.send(
                        '❌ 無法載入播放清單，請確認連結是否正確。')
                    return

                if not tracks:
                    await interaction.followup.send('❌ 播放清單是空的或無法讀取。')
                    return

                tag = self._next_playlist_tag(interaction.guild_id)
                tracks = [t._replace(playlist_tag=tag) for t in tracks]

                async with self._get_play_lock(interaction.guild_id):
                    vc = interaction.guild.voice_client
                    if vc is None or not vc.is_connected():
                        vc = await interaction.user.voice.channel.connect()

                    queue = self.get_queue(interaction.guild_id)
                    if vc.is_playing() or vc.is_paused():
                        if insert_next:
                            # Insert at front: new_tracks + existing_queue
                            new_queue = deque(tracks)
                            new_queue.extend(queue)
                            self.queues[interaction.guild_id] = new_queue
                            queue = new_queue
                            status = '⏭️ 插播播放清單'
                        else:
                            queue.extend(tracks)
                            status = '✅ 已加入播放清單'
                        embed = discord.Embed(
                            title=status,
                            description=f'新增了 **{len(tracks)}** 首歌曲（清單 `#{tag}`）',
                            color=discord.Color.green(),
                        )
                        embed.add_field(name='佇列總數',
                                        value=f'{len(queue)} 首', inline=True)
                        await interaction.followup.send(embed=embed)
                        self._schedule_prefetch(interaction.guild_id)
                    else:
                        first = tracks[0]
                        rest = tracks[1:]
                        try:
                            url = await self._resolve_stream(interaction.guild_id, first)
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
                        if insert_next:
                            new_queue = deque(rest)
                            new_queue.extend(queue)
                            self.queues[interaction.guild_id] = new_queue
                        else:
                            queue.extend(rest)
                        view = MusicControls(self, interaction.guild)
                        embed = self.make_embed(first)
                        if rest:
                            embed.add_field(
                                name='📋 播放清單',
                                value=f'另有 {len(rest)} 首已加入佇列（清單 `#{tag}`）',
                                inline=True)
                        msg = await interaction.followup.send(
                            embed=embed, view=view)
                        view.message = msg
                        self._schedule_prefetch(interaction.guild_id)
                return

            # ── Single track ──────────────────────────────────────
            try:
                track = await asyncio.get_running_loop().run_in_executor(
                    None, self.fetch_audio, query)
            except Exception:
                log.exception('fetch_audio failed: %s', query)
                await interaction.followup.send(
                    '❌ 找不到該歌曲，請換個關鍵字或確認連結是否正確。')
                return

            async with self._get_play_lock(interaction.guild_id):
                vc = interaction.guild.voice_client
                if vc is None or not vc.is_connected():
                    vc = await interaction.user.voice.channel.connect()

                queue = self.get_queue(interaction.guild_id)
                if vc.is_playing() or vc.is_paused():
                    if insert_next:
                        queue.appendleft(track)
                        embed = self.make_embed(track, status='⏭️ 插播 — 下一首播放',
                                                color=discord.Color.orange())
                    else:
                        queue.append(track)
                        embed = self.make_embed(track, status='✅ 已加入佇列',
                                                color=discord.Color.green())
                        embed.add_field(name='佇列位置', value=f'#{len(queue)}', inline=True)
                    await interaction.followup.send(embed=embed)
                    self._schedule_prefetch(interaction.guild_id)
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

    # ── /play ─────────────────────────────────────────────────────────
    @app_commands.command(name='play', description='播放音樂（YouTube / Spotify 網址或歌名）')
    @app_commands.describe(query='歌名、YouTube 網址或 Spotify 網址')
    async def play(self, interaction: discord.Interaction, query: str):
        await self._play_impl(interaction, query, insert_next=False)

    # ── /playnext ─────────────────────────────────────────────────────
    @app_commands.command(name='playnext', description='插播 — 將歌曲插入佇列最前面，下一首播放')
    @app_commands.describe(query='歌名、YouTube 網址或 Spotify 網址')
    async def playnext(self, interaction: discord.Interaction, query: str):
        await self._play_impl(interaction, query, insert_next=True)

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
            await interaction.response.send_message('⏹️ 已停止播放並離開語音頻道。')
            await self._safe_disconnect(vc)
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
        if position == 1:
            self._schedule_prefetch(interaction.guild_id)  # front changed
        await interaction.response.send_message(
            f'🗑️ 已移除第 {position} 首：**{removed.title}**')

    # ── /remove-playlist ─────────────────────────────────────────────
    @app_commands.command(name='remove-playlist', description='移除佇列中整個 YouTube 播放清單')
    @app_commands.describe(tag='播放清單編號（不填則顯示清單列表）')
    async def remove_playlist(self, interaction: discord.Interaction, tag: int = None):
        queue = self.get_queue(interaction.guild_id)
        if not queue:
            await interaction.response.send_message('📭 佇列是空的。', ephemeral=True)
            return

        # Find all playlist tags in queue
        tags_in_queue: dict[int, dict] = {}
        for t in queue:
            if t.playlist_tag is not None:
                if t.playlist_tag not in tags_in_queue:
                    tags_in_queue[t.playlist_tag] = {'count': 0, 'first': t.title}
                tags_in_queue[t.playlist_tag]['count'] += 1

        if not tags_in_queue:
            await interaction.response.send_message('佇列中沒有播放清單的歌曲。', ephemeral=True)
            return

        if tag is None:
            # Show list
            desc = '\n'.join(
                f'`#{t}` — **{info["first"]}** 等 {info["count"]} 首'
                for t, info in sorted(tags_in_queue.items())
            )
            embed = discord.Embed(
                title='📋 佇列中的播放清單',
                description=desc,
                color=discord.Color.blue(),
            )
            embed.set_footer(text='使用 /remove-playlist <編號> 來移除整個清單')
            await interaction.response.send_message(embed=embed)
            return

        if tag not in tags_in_queue:
            await interaction.response.send_message(
                f'❌ 找不到播放清單 `#{tag}`', ephemeral=True)
            return

        new_queue = deque(t for t in queue if t.playlist_tag != tag)
        removed = len(queue) - len(new_queue)
        self.queues[interaction.guild_id] = new_queue
        self._schedule_prefetch(interaction.guild_id)
        await interaction.response.send_message(
            f'🗑️ 已移除播放清單 `#{tag}`（共 {removed} 首）')

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
        self._schedule_prefetch(interaction.guild_id)  # front likely changed
        await interaction.response.send_message(f'🔀 已隨機排列 {len(lst)} 首歌曲！')

    # ── /autoplay ──────────────────────────────────────────────────────
    @app_commands.command(name='autoplay', description='切換自動推薦播放（佇列結束後自動播放相關歌曲）')
    async def autoplay_cmd(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        self.autoplay[guild_id] = not self.autoplay.get(guild_id, True)
        status = '開啟 🎶' if self.autoplay[guild_id] else '關閉'
        await interaction.response.send_message(f'自動推薦播放已{status}')

    # ── Auto-disconnect when channel is empty (with grace period) ─────
    LEAVE_GRACE_SECONDS = 30

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member,
                                    before: discord.VoiceState,
                                    after: discord.VoiceState):
        if member.bot:
            return
        guild = member.guild
        vc = guild.voice_client
        if not vc:
            return

        alone = len(vc.channel.members) == 1  # only the bot left
        existing = self._leave_tasks.get(guild.id)

        if alone:
            if existing is None or existing.done():
                self._leave_tasks[guild.id] = self.bot.loop.create_task(
                    self._leave_after_grace(guild))
        else:
            # Someone is (back) in the channel: cancel any pending leave
            if existing and not existing.done():
                existing.cancel()
            self._leave_tasks.pop(guild.id, None)

    async def _leave_after_grace(self, guild: discord.Guild):
        try:
            await asyncio.sleep(self.LEAVE_GRACE_SECONDS)
            vc = guild.voice_client
            if vc and len(vc.channel.members) == 1:
                self._cleanup_guild(guild.id)
                await self._safe_disconnect(vc)
        except asyncio.CancelledError:
            pass
        finally:
            self._leave_tasks.pop(guild.id, None)


async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCog(bot))
