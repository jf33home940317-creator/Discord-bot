$KEY = "$env:USERPROFILE\Downloads\ssh-key-2026-05-31.key"

Write-Host "=== Testing yt-dlp directly ===" -ForegroundColor Cyan
ssh -i $KEY -o StrictHostKeyChecking=no ubuntu@161.33.158.172 @"
/home/ubuntu/miniconda3/bin/yt-dlp -f 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best' --no-playlist --no-warnings --print '%(title)s' --print '%(id)s' --print '%(duration_string)s' -g 'ytsearch1:test music' 2>&1 | head -20
"@
