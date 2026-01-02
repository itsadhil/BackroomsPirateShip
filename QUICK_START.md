# ⚡ Quick Start Guide - Oracle Cloud

Too long didn't read? Here's the lightning-fast version:

## 1️⃣ Create Oracle Cloud Account
- Go to https://www.oracle.com/cloud/free/
- Sign up (credit card needed but FREE)
- Choose region close to you

## 2️⃣ Create VM Instance
- Click "Create VM Instance"
- **Image:** Canonical Ubuntu 22.04 or 24.04
- **Shape:** Click "Change Shape"
  - **Click the "Ampere" tab** (not AMD!)
  - Click **VM.Standard.A1.Flex** (shows "1 (80 max) OCPU")
  - Move sliders:
    - **OCPUs: 4** (use maximum free)
    - **Memory: 24 GB** (use maximum free)
  - Click "Select Shape"
  - If no Ampere: Use **VM.Standard.E2.1.Micro** (AMD, 1GB RAM)
- **Networking:** Keep defaults (create new VCN, assign public IP)
- **Security:** Keep all defaults (encryption enabled)
- **SSH Keys:** Generate and download both keys
- **Advanced Options:** Skip it (leave all defaults)
- Click "Create"
- Wait for "RUNNING" status (green)
- **Copy Public IP**

## 3️⃣ Connect via SSH

**Windows (PuTTY):**
1. Download PuTTY from https://www.putty.org/
2. Convert key with PuTTYgen (Load key → Save private key as .ppk)
3. Open PuTTY:
   - Host: `ubuntu@YOUR_IP`
   - Auth: Load your .ppk file
   - Click Open

**Mac/Linux:**
```bash
chmod 400 ~/path/to/oracle-key
ssh -i ~/path/to/oracle-key ubuntu@YOUR_IP
```

## 4️⃣ Run Setup Script
```bash
# Copy setup script
curl -O https://raw.githubusercontent.com/itsadhil/BackroomsPirateShip/main/setup-oracle.sh

# Make it executable
chmod +x setup-oracle.sh

# Run it
./setup-oracle.sh
```

## 5️⃣ Create .env File
```bash
cd ~/BackroomsPirateShip
nano .env
```

Paste:
```env
DISCORD_TOKEN=your_bot_token
TWITCH_CLIENT_ID=your_client_id
TWITCH_CLIENT_SECRET=your_secret
ENABLE_RSS_AUTO=true
```

Save: `Ctrl+X`, `Y`, `Enter`

## 6️⃣ Test Run
```bash
source venv/bin/activate
python3 bot.py
```

See "Logged in as..."? **Success!** Press `Ctrl+C` to stop.

## 7️⃣ Setup Auto-Start
```bash
# Create service file
sudo nano /etc/systemd/system/discord-bot.service
```

Paste:
```ini
[Unit]
Description=Discord Pirate Ship Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/BackroomsPirateShip
Environment="PATH=/home/ubuntu/BackroomsPirateShip/venv/bin:/usr/bin"
ExecStart=/home/ubuntu/BackroomsPirateShip/venv/bin/python3 /home/ubuntu/BackroomsPirateShip/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Save: `Ctrl+X`, `Y`, `Enter`

```bash
# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable discord-bot
sudo systemctl start discord-bot

# Check status
sudo systemctl status discord-bot
```

## 🎉 Done!

Your bot is now running 24/7!

### Useful Commands:
```bash
# View logs
sudo journalctl -u discord-bot -f

# Restart bot
sudo systemctl restart discord-bot

# Stop bot
sudo systemctl stop discord-bot

# Update bot
cd ~/BackroomsPirateShip
git pull
sudo systemctl restart discord-bot
```

---

**Problems?** Check [ORACLE_SETUP.md](ORACLE_SETUP.md) for detailed troubleshooting.
