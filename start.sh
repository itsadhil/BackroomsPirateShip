#!/bin/bash
set -e

echo "🔍 Checking Playwright installation..."

# Install Playwright browsers if not already installed
if [ ! -d "/root/.cache/ms-playwright/chromium_headless_shell-1200" ]; then
    echo "📦 Installing Playwright Chromium browser..."
    python -m playwright install --with-deps chromium
    echo "✅ Playwright installation complete!"
else
    echo "✅ Playwright already installed"
fi

# Ensure data directory exists
mkdir -p data logs

echo "🚀 Starting bot..."
python bot.py
