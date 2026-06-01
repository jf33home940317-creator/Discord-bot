# deploy_oracle.ps1 — 部署 Discord music bot 到 Oracle VM
$VM_IP   = "161.33.158.172"
$VM_USER = "ubuntu"
$KEY     = "$env:USERPROFILE\Downloads\ssh-key-2026-05-31.key"
$PROJECT = "E:\93050207\discord-bot"
$REMOTE  = "/home/ubuntu/discord_bot"

Write-Host "[1] Packing bot files..." -ForegroundColor Cyan
$TMP_ZIP = "$env:TEMP\discord_bot.zip"
if (Test-Path $TMP_ZIP) { Remove-Item $TMP_ZIP }

Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($TMP_ZIP, 'Create')
foreach ($f in @("main.py", "music.py", "requirements.txt")) {
    $full = Join-Path $PROJECT $f
    if (Test-Path $full) {
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $full, $f) | Out-Null
    }
}
$zip.Dispose()
Write-Host "   Packed." -ForegroundColor Green

Write-Host "[2] Upload files..." -ForegroundColor Cyan
ssh -i $KEY -o StrictHostKeyChecking=no "${VM_USER}@${VM_IP}" "mkdir -p $REMOTE"
scp -i $KEY -o StrictHostKeyChecking=no $TMP_ZIP "${VM_USER}@${VM_IP}:/tmp/discord_bot.zip"
ssh -i $KEY -o StrictHostKeyChecking=no "${VM_USER}@${VM_IP}" "cd $REMOTE && unzip -o /tmp/discord_bot.zip"

Write-Host "[3] Upload .env..." -ForegroundColor Cyan
scp -i $KEY -o StrictHostKeyChecking=no "$PROJECT\.env" "${VM_USER}@${VM_IP}:$REMOTE/.env"

Write-Host "[4] Install ffmpeg + packages (takes 2-3 min)..." -ForegroundColor Cyan
scp -i $KEY -o StrictHostKeyChecking=no "$PSScriptRoot\bot_setup.sh" "${VM_USER}@${VM_IP}:/tmp/bot_setup.sh"
ssh -i $KEY -o StrictHostKeyChecking=no "${VM_USER}@${VM_IP}" "chmod +x /tmp/bot_setup.sh && /tmp/bot_setup.sh"

Write-Host "[5] Status:" -ForegroundColor Cyan
ssh -i $KEY -o StrictHostKeyChecking=no "${VM_USER}@${VM_IP}" "sudo systemctl status discord-bot --no-pager -l && sudo journalctl -u discord-bot -n 15 --no-pager"
