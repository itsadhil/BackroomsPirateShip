# 🚀 Oracle Cloud Setup Guide - Complete A-Z

## 📋 **What You'll Get**
- Free Ubuntu VM running 24/7
- Your Discord bot auto-starting on boot
- Automatic restarts if it crashes
- Easy log viewing and management

---

## **STEP 1: Create Oracle Cloud Account**

### 1.1 Sign Up (5 minutes)
1. Go to https://www.oracle.com/cloud/free/
2. Click **"Start for free"**
3. Fill in:
   - **Email** (use your real email)
   - **Country**
   - **Account name** (choose any name)
4. Verify email
5. Enter payment card (Required but **won't be charged** - it's for verification only)
6. Complete verification

### 1.2 Choose Region
- **IMPORTANT:** Choose a region close to you or your users
- Popular: `US East (Ashburn)`, `UK South (London)`, `Germany Central (Frankfurt)`
- You **cannot change region** later!

✅ **You now have:** Free Oracle Cloud account with $300 credits + Always Free resources

---

## **STEP 2: Create Your VM Instance**

### 2.1 Navigate to Compute
1. After login, you'll see Oracle Cloud dashboard
2. Click **"Create a VM instance"** (big blue button)
   - OR: Go to ☰ Menu → **Compute** → **Instances** → **Create Instance**

### 2.2 Configure Instance

#### **Name Your Instance**
```
Name: discord-bot
or anything you like
```

#### **Choose Compartment**
- Leave as **"root"** (default)

#### **Placement**
- Leave as default (your region)

#### **Image and Shape** (MOST IMPORTANT PART)

**Option A: ARM Ampere (RECOMMENDED - Better specs)**
1. Click **"Change Image"**
   - Select **"Canonical Ubuntu"** (22.04 or latest)
   - Click **"Select Image"**

2. Click **"Change Shape"**
   - Select **"Ampere"** (not AMD)
   - Choose **"VM.Standard.A1.Flex"**
   - Set:
     - **OCPUs:** 2 (or up to 4)
     - **Memory (GB):** 12 (or up to 24)
   - Click **"Select Shape"**

**Option B: AMD (Simpler)**
1. Image: **Canonical Ubuntu 22.04**
2. Shape: **VM.Standard.E2.1.Micro** (Always Free)

#### **Networking**
- ✅ Keep **"Create new virtual cloud network"** checked
- ✅ Keep **"Assign a public IPv4 address"** checked
- Leave everything else default

#### **Add SSH Keys** (VERY IMPORTANT)

**Windows Users:**
1. Click **"Generate a key pair for me"**
2. Click **"Save Private Key"** → Save to `C:\Users\YourName\.ssh\oracle-key`
3. Click **"Save Public Key"** → Save to `C:\Users\YourName\.ssh\oracle-key.pub`

**Note:** Remember where you saved these files!

#### **Boot Volume**
- Leave default (50GB is free)

### 2.3 Create Instance
1. Click **"Create"** button (bottom)
2. Wait 2-3 minutes for provisioning
3. Status will change from **"PROVISIONING"** to **"RUNNING"** (green)

✅ **You now have:** A running Ubuntu VM in Oracle Cloud!

---

## **STEP 3: Open Firewall Ports**

Your bot needs to connect to Discord, but Oracle blocks traffic by default.

### 3.1 Add Ingress Rules (Optional - for web dashboard later)
1. On your instance page, find **"Primary VNIC"** section
2. Click on the **Subnet** link (looks like: `subnet-xxxxx`)
3. Click **"Default Security List"**
4. Click **"Add Ingress Rules"**
5. Add this rule:
   - **Source CIDR:** `0.0.0.0/0`
   - **IP Protocol:** `TCP`
   - **Destination Port Range:** `80,443`
   - **Description:** `Allow HTTP/HTTPS`
6. Click **"Add Ingress Rules"**

### 3.2 Note Your Public IP
- Go back to your instance page
- Find **"Public IP address"** (e.g., `123.45.67.89`)
- **Copy this IP** - you'll need it!

✅ **You now have:** Open ports for your bot

---

## **STEP 4: Connect to Your VM**

### 4.1 Windows Users - Install PuTTY

#### Download PuTTY
1. Go to https://www.putty.org/
2. Download **putty-64bit-installer.msi**
3. Install it

#### Convert SSH Key
1. Open **PuTTYgen** (search in Windows start menu)
2. Click **"Load"**
3. Change file filter to **"All Files (*.*)"**
4. Select your `oracle-key` file (the one without .pub)
5. Click **"Save private key"** → Save as `oracle-key.ppk`
6. Close PuTTYgen

#### Connect with PuTTY
1. Open **PuTTY**
2. In **Session**:
   - **Host Name:** `ubuntu@YOUR_PUBLIC_IP` (e.g., `ubuntu@123.45.67.89`)
   - **Port:** `22`
   - **Connection type:** `SSH`
3. In left menu, go to **Connection → SSH → Auth → Credentials**:
   - Click **"Browse"** for **"Private key file"**
   - Select your `oracle-key.ppk` file
4. (Optional) Go back to **Session**:
   - **Saved Sessions:** type `discord-bot`
   - Click **"Save"** (so you don't repeat steps)
5. Click **"Open"**
6. Click **"Accept"** if you see security alert
7. You're in! You should see: `ubuntu@instance-name:~$`

### 4.2 Mac/Linux Users
```bash
# Set correct permissions
chmod 400 ~/path/to/oracle-key

# Connect
ssh -i ~/path/to/oracle-key ubuntu@YOUR_PUBLIC_IP
```

✅ **You now have:** Terminal access to your VM!

---

## **STEP 5: Install Dependencies**

Copy and paste these commands one by one:

### 5.1 Update System
```bash
sudo apt update && sudo apt upgrade -y
```
*(Takes 2-3 minutes)*

### 5.2 Install Python and Tools
```bash
sudo apt install -y python3 python3-pip python3-venv git curl
```

### 5.3 Install Playwright Dependencies
```bash
sudo apt install -y \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2
```

### 5.4 Install Node.js (for Playwright)
```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

✅ **You now have:** All required software installed!

---

## **STEP 6: Setup Your Bot**

### 6.1 Clone Your Repository
```bash
cd ~
git clone https://github.com/itsadhil/BackroomsPirateShip.git
cd BackroomsPirateShip
```

### 6.2 Create Environment File
```bash
nano .env
```

Paste this (replace with your real values):
```env
DISCORD_TOKEN=your_discord_bot_token_here
TWITCH_CLIENT_ID=your_twitch_client_id_here
TWITCH_CLIENT_SECRET=your_twitch_client_secret_here
ENABLE_RSS_AUTO=true
```

**Save and exit:**
- Press `Ctrl + X`
- Press `Y`
- Press `Enter`

### 6.3 Create Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate
```

You should see `(venv)` in your prompt now.

### 6.4 Install Python Packages
```bash
pip install -r requirements.txt
```
*(Takes 3-5 minutes)*

### 6.5 Install Playwright Browsers
```bash
playwright install chromium
```
*(Takes 2-3 minutes)*

### 6.6 Test Run
```bash
python3 bot.py
```

**If you see:**
```
✅ Logged in as YourBotName
✅ Commands synced to guild ID: ...
```

**Success!** Press `Ctrl + C` to stop it.

✅ **You now have:** Working bot on Oracle Cloud!

---

## **STEP 7: Setup Auto-Start with Systemd**

This makes your bot run automatically and restart if it crashes.

### 7.1 Create Service File
```bash
sudo nano /etc/systemd/system/discord-bot.service
```

Paste this (replace `YOUR_USERNAME` with `ubuntu` if you're using default):
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

**Save:** `Ctrl + X`, `Y`, `Enter`

### 7.2 Enable and Start Service
```bash
# Reload systemd to read new service
sudo systemctl daemon-reload

# Enable bot to start on boot
sudo systemctl enable discord-bot

# Start the bot now
sudo systemctl start discord-bot

# Check status
sudo systemctl status discord-bot
```

**You should see:**
```
● discord-bot.service - Discord Pirate Ship Bot
   Active: active (running) since ...
```

Press `q` to exit status view.

✅ **You now have:** Bot running 24/7 with auto-restart!

---

## **STEP 8: Management Commands**

### View Bot Status
```bash
sudo systemctl status discord-bot
```

### View Live Logs (follow mode)
```bash
sudo journalctl -u discord-bot -f
```
Press `Ctrl + C` to stop viewing.

### View Last 100 Lines of Logs
```bash
sudo journalctl -u discord-bot -n 100
```

### Restart Bot
```bash
sudo systemctl restart discord-bot
```

### Stop Bot
```bash
sudo systemctl stop discord-bot
```

### Start Bot
```bash
sudo systemctl start discord-bot
```

### Disable Auto-Start
```bash
sudo systemctl disable discord-bot
```

---

## **STEP 9: Update Your Bot**

When you push new code to GitHub:

```bash
# Connect to your VM (PuTTY or SSH)

# Stop the bot
sudo systemctl stop discord-bot

# Navigate to bot directory
cd ~/BackroomsPirateShip

# Pull latest changes
git pull origin main

# Activate virtual environment
source venv/bin/activate

# Update dependencies (if requirements.txt changed)
pip install -r requirements.txt

# Start the bot
sudo systemctl start discord-bot

# Check if it's running
sudo systemctl status discord-bot
```

---

## **STEP 10: Firewall Configuration (Ubuntu)**

Oracle has firewall rules, but Ubuntu also has its own firewall:

```bash
# Allow SSH (important!)
sudo ufw allow 22/tcp

# Allow HTTP/HTTPS (if you add web dashboard later)
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Enable firewall
sudo ufw enable

# Check status
sudo ufw status
```

---

## **🎉 YOU'RE DONE!**

Your bot is now:
- ✅ Running 24/7 on Oracle Cloud
- ✅ Auto-starts on server reboot
- ✅ Auto-restarts if it crashes
- ✅ Completely free forever

---

## **Troubleshooting**

### Bot Won't Start
```bash
# Check detailed error logs
sudo journalctl -u discord-bot -n 50 --no-pager

# Common issues:
# 1. Wrong Discord token → check .env file
# 2. Missing dependencies → reinstall: pip install -r requirements.txt
# 3. Permission issues → make sure files are owned by ubuntu user
```

### Can't Connect via SSH
- Check your VM is **RUNNING** (green) in Oracle Console
- Verify you're using correct Public IP
- Make sure you're using the right SSH key
- Try creating a new SSH key pair from Oracle Console

### Out of Memory
```bash
# Check memory usage
free -h

# If low, consider:
# 1. Upgrading to ARM Ampere instance (24GB free)
# 2. Adding swap space
```

### Add Swap Space (if needed)
```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### Python Module Errors
```bash
cd ~/BackroomsPirateShip
source venv/bin/activate
pip install --upgrade -r requirements.txt
playwright install chromium
```

---

## **Useful Tips**

### Keep Terminal Session Alive
If you want to run commands that take a long time, use `tmux`:

```bash
# Install tmux
sudo apt install tmux

# Start new session
tmux new -s bot

# Do your work...

# Detach from session: Ctrl + B, then D

# List sessions
tmux ls

# Reattach to session
tmux attach -t bot
```

### Monitor System Resources
```bash
# Install htop
sudo apt install htop

# Run it
htop

# Press F10 or Q to quit
```

### Backup Your Data Files
```bash
# Create backup
cd ~/BackroomsPirateShip
tar -czf backup-$(date +%Y%m%d).tar.gz *.json

# Download to your PC via SCP (from your local PC):
scp -i path/to/oracle-key ubuntu@YOUR_IP:~/BackroomsPirateShip/backup-*.tar.gz ./
```

---

## **Need Help?**

- **Oracle Cloud Docs:** https://docs.oracle.com/en-us/iaas/
- **Ubuntu Help:** https://help.ubuntu.com/
- **Discord.py Docs:** https://discordpy.readthedocs.io/

---

**Created:** January 2, 2026
**For:** Backrooms Pirate Ship Discord Bot
**Platform:** Oracle Cloud Always Free Tier
