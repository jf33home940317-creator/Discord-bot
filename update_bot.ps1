# update_bot.ps1
$KEY = "$env:USERPROFILE\Downloads\ssh-key-2026-05-31.key"

Write-Host "Uploading fixed music.py..." -ForegroundColor Cyan
scp -i $KEY -o StrictHostKeyChecking=no `
    "E:\93050207\discord-bot\music.py" `
    "ubuntu@161.33.158.172:/home/ubuntu/discord_bot/music.py"

Write-Host "Updating yt-dlp to latest..." -ForegroundColor Cyan
ssh -i $KEY -o StrictHostKeyChecking=no ubuntu@161.33.158.172 `
    "/home/ubuntu/miniconda3/bin/pip install -q -U yt-dlp"

Write-Host "Restarting bot..." -ForegroundColor Cyan
ssh -i $KEY -o StrictHostKeyChecking=no ubuntu@161.33.158.172 `
    "sudo systemctl restart discord-bot"

Start-Sleep -Seconds 8
Write-Host "Logs:" -ForegroundColor Cyan
ssh -i $KEY -o StrictHostKeyChecking=no ubuntu@161.33.158.172 `
    "sudo journalctl -u discord-bot -n 15 --no-pager"
