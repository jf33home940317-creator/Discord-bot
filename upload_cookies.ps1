# upload_cookies.ps1
$KEY = "$env:USERPROFILE\Downloads\ssh-key-2026-05-31.key"

Write-Host "Uploading cookies.txt to VM..." -ForegroundColor Cyan
scp -i $KEY -o StrictHostKeyChecking=no `
    "$env:USERPROFILE\Downloads\cookies.txt" `
    "ubuntu@161.33.158.172:/home/ubuntu/discord_bot/cookies.txt"

Write-Host "Testing yt-dlp with cookies..." -ForegroundColor Cyan
ssh -i $KEY -o StrictHostKeyChecking=no ubuntu@161.33.158.172 `
    "/home/ubuntu/miniconda3/bin/yt-dlp --cookies /home/ubuntu/discord_bot/cookies.txt --print '%(title)s' -g 'ytsearch1:test music' 2>&1 | head -5"

Write-Host "Done. Run .\update_bot.ps1 after confirming cookies work." -ForegroundColor Green
