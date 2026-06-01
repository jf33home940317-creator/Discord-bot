#!/bin/bash
set -e

PYTHON=/home/ubuntu/miniconda3/bin/python
PIP=/home/ubuntu/miniconda3/bin/pip
REMOTE=/home/ubuntu/discord_bot

echo "[1] Install ffmpeg..."
sudo apt-get install -y ffmpeg

echo "[2] Install Python packages..."
$PIP install -q discord.py[voice] yt-dlp spotipy PyNaCl python-dotenv

echo "[3] Verify imports..."
$PYTHON -c "import discord, yt_dlp, spotipy, nacl; print('All imports OK')"

echo "[4] Write systemd service..."
sudo tee /etc/systemd/system/discord-bot.service > /dev/null << EOF
[Unit]
Description=Discord Music Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=$REMOTE
ExecStart=$PYTHON $REMOTE/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo "[5] Enable and start..."
sudo systemctl daemon-reload
sudo systemctl enable discord-bot
sudo systemctl start discord-bot
sleep 8

echo "[6] Status:"
sudo systemctl status discord-bot --no-pager -l
