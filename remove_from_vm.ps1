# remove_from_vm.ps1
$KEY = "$env:USERPROFILE\Downloads\ssh-key-2026-05-31.key"

ssh -i $KEY -o StrictHostKeyChecking=no ubuntu@161.33.158.172 @"
sudo systemctl stop discord-bot
sudo systemctl disable discord-bot
sudo rm /etc/systemd/system/discord-bot.service
sudo systemctl daemon-reload
rm -rf /home/ubuntu/discord_bot
echo "Discord bot removed."
"@
