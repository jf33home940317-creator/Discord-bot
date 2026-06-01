# Discord Music Bot

Discord 語音頻道音樂機器人，支援 YouTube / Spotify 搜尋與播放。

## 功能

- `/play <歌名或URL>` — 播放音樂（YouTube / Spotify 單曲 / YouTube 播放清單）
- `/skip` — 跳過目前這首
- `/stop` — 停止播放並離開語音頻道
- `/queue [頁碼]` — 顯示播放清單（每頁 10 首）
- `/pause` / `/resume` — 暫停 / 繼續播放
- `/nowplaying` — 顯示目前播放的歌曲（含互動按鈕）
- `/loop` — 切換循環播放模式
- `/shuffle` — 隨機洗牌播放清單
- `/remove <編號>` — 刪除播放清單中指定歌曲
- `/autoplay` — 切換自動推薦播放（佇列結束時自動接歌）
- `/help` — 顯示指令說明

### 互動按鈕

播放時會顯示控制面板，可直接點擊：暫停/繼續、跳過、循環、洗牌、停止。

## 環境需求

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/download.html)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)

## 安裝

```bash
pip install -r requirements.txt
```

## 設定

建立 `.env` 檔案：

```env
DISCORD_TOKEN=你的_Discord_Bot_Token
SPOTIFY_CLIENT_ID=你的_Spotify_Client_ID
SPOTIFY_CLIENT_SECRET=你的_Spotify_Client_Secret
```

可選環境變數（有預設值，通常不需要設）：

| 變數 | 說明 | 預設值 |
|------|------|--------|
| `FFMPEG_PATH` | FFmpeg 執行檔路徑 | Windows: `C:\ffmpeg\bin\ffmpeg.exe` / Linux: `ffmpeg` |
| `YTDLP_PATH` | yt-dlp 執行檔路徑 | Windows: `yt-dlp` / Linux: `/home/ubuntu/miniconda3/bin/yt-dlp` |

## 啟動

```bash
python main.py
```

Windows 也可以雙擊 `run.bat`。

## 專案結構

```
main.py              # Bot 進入點、/help 指令
music.py             # 音樂播放核心（MusicCog + MusicControls）
requirements.txt     # Python 依賴
.env                 # 環境變數（不上傳）
run.bat              # Windows 本機啟動
```

## License

MIT
