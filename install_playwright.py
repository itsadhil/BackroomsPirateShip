#!/usr/bin/env python3
"""Post-install script to install Playwright browsers."""
import subprocess
import sys

def main():
    print("=" * 60)
    print("Installing Playwright Chromium browser...")
    print("=" * 60)
    
    try:
        subprocess.check_call([
            sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"
        ])
        print("✅ Playwright installation complete!")
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to install Playwright: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
