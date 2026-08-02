# Discord 音樂 Bot 設計文件

**日期：** 2026-05-27
**用途：** 私人好友伺服器（綜合型）
**托管平台：** Discloud（免費，無需信用卡）

---

## 1. 目標

建立一個 Discord 音樂播放 Bot，支援 YouTube 和 Spotify 連結播放，具備播放清單管理功能，部署到 Discloud 實現 24 小時在線。

## 2. 技術選型

| 項目 | 選擇 | 原因 |
|---|---|---|
| Bot 框架 | discord.py | 文件豐富、純 Python、易維護 |
| 音訊抓取 | yt-dlp | 支援 YouTube、穩定更新 |
| Spotify 解析 | spotipy | 官方 Spotify API 封裝 |
| 托管 | Discloud | 免費、專為 Discord Bot 設計 |
| 語音 | PyNaCl | discord.py 語音必要依賴 |

## 3. 指令清單

| 指令 | 說明 |
|---|---|
| `/play <歌名或URL>` | 播放音樂，支援 YouTube 網址、Spotify 網址、歌名搜尋 |
| `/skip` | 跳過目前播放的歌曲 |
| `/stop` | 停止播放並讓 Bot 離開語音頻道 |
| `/queue` | 顯示目前的播放清單 |
| `/pause` | 暫停播放 |
| `/resume` | 繼續播放 |
| `/nowplaying` | 顯示目前播放的歌曲名稱與資訊 |
| `/help` | 顯示所有指令與說明 |

## 4. Spotify 處理邏輯

Spotify API 不提供音訊串流，因此採用以下流程：

```
使用者輸入 Spotify 連結
  → spotipy 解析 → 取得歌名 + 歌手名稱
  → yt-dlp 搜尋 YouTube「歌名 歌手」
  → 播放 YouTube 音訊串流
```

## 5. 檔案結構

```
discord-bot/
├── main.py           ← Bot 主程式、Slash 指令註冊、事件處理
├── music.py          ← 音樂功能邏輯（播放、佇列、Spotify 解析）
├── requirements.txt  ← 套件清單
├── discloud.config   ← Discloud 部署設定
└── .env              ← API Token（本機用，不上傳）
```

### main.py 職責
- 初始化 Bot、載入 music.py 的 Cog
- 處理 `/help` 指令
- 錯誤處理

### music.py 職責
- `MusicCog` 類別：處理所有音樂指令
- 播放佇列（queue）管理
- Spotify URL 偵測與解析
- yt-dlp 音訊串流

## 6. 需要申請的 API

| 服務 | 取得方式 | 用途 |
|---|---|---|
| Discord Bot Token | Discord Developer Portal → New Application → Bot | Bot 登入 |
| Spotify Client ID/Secret | Spotify for Developers → Create App | 解析 Spotify 連結 |

## 7. 套件清單（requirements.txt）

```
discord.py[voice]
yt-dlp
spotipy
PyNaCl
python-dotenv
```

## 8. 部署流程

1. 本機開發並測試
2. 將所有檔案（不含 .env）打包成 .zip
3. 上傳到 Discloud
4. 在 Discloud 設定環境變數（DISCORD_TOKEN、SPOTIFY_CLIENT_ID、SPOTIFY_CLIENT_SECRET）
5. Bot 上線，24 小時運行
