#!/bin/bash
# Automatic setup script for Oracle Cloud Ubuntu VM
# Run this after connecting to your VM for the first time

set -e  # Exit on any error

echo "=========================================="
echo "🚀 Discord Bot - Oracle Cloud Setup"
echo "=========================================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as ubuntu user
if [ "$USER" != "ubuntu" ]; then
    echo -e "${YELLOW}⚠️  This script should be run as 'ubuntu' user${NC}"
    echo "Current user: $USER"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Step 1: Update system
echo -e "${GREEN}[1/8] Updating system...${NC}"
sudo apt update
sudo apt upgrade -y

# Step 2: Install basic tools
echo -e "${GREEN}[2/8] Installing Python and tools...${NC}"
sudo apt install -y python3 python3-pip python3-venv git curl wget

# Step 3: Install Playwright dependencies
echo -e "${GREEN}[3/8] Installing Playwright dependencies...${NC}"
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
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libgtk-3-0

# Step 4: Install Node.js
echo -e "${GREEN}[4/8] Installing Node.js...${NC}"
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# Step 5: Clone repository
echo -e "${GREEN}[5/8] Cloning bot repository...${NC}"
cd ~
if [ -d "BackroomsPirateShip" ]; then
    echo "Repository already exists. Pulling latest changes..."
    cd BackroomsPirateShip
    git pull
else
    git clone https://github.com/itsadhil/BackroomsPirateShip.git
    cd BackroomsPirateShip
fi

# Step 6: Setup Python environment
echo -e "${GREEN}[6/8] Setting up Python virtual environment...${NC}"
python3 -m venv venv
source venv/bin/activate

# Step 7: Install Python packages
echo -e "${GREEN}[7/8] Installing Python packages (this may take a few minutes)...${NC}"
pip install --upgrade pip
pip install -r requirements.txt

# Step 8: Install Playwright browsers
echo -e "${GREEN}[8/8] Installing Playwright browsers...${NC}"
python -m playwright install --with-deps chromium

# Step 9: Create data and logs directories
echo -e "${GREEN}[9/9] Creating data directories...${NC}"
mkdir -p data logs

echo ""
echo -e "${GREEN}=========================================="
echo "✅ Setup Complete!"
echo "==========================================${NC}"
echo ""
echo "Next steps:"
echo "1. Create your .env file:"
echo "   nano ~/BackroomsPirateShip/.env"
echo ""
echo "2. Add these variables:"
echo "   DISCORD_TOKEN=your_token_here"
echo "   TWITCH_CLIENT_ID=your_id_here"
echo "   TWITCH_CLIENT_SECRET=your_secret_here"
echo "   ENABLE_RSS_AUTO=true"
echo ""
echo "3. Run data migration (if needed):"
echo "   cd ~/BackroomsPirateShip"
echo "   source venv/bin/activate"
echo "   python3 migrate_data.py"
echo ""
echo "4. Test run the bot:"
echo "   python3 bot.py"
echo ""
echo "5. Setup systemd service (auto-start):"
echo "   sudo nano /etc/systemd/system/discord-bot.service"
echo "   (See discord-bot.service file for content)"
echo ""
echo -e "${YELLOW}Need help? Check ORACLE_SETUP.md for detailed instructions!${NC}"
