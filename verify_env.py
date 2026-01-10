#!/usr/bin/env python3
"""
Verify .env file configuration for AI Assistant
Run this on your server to check if the API key is being read correctly
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Get the bot directory
bot_dir = Path(__file__).parent
os.chdir(bot_dir)

print("=" * 70)
print("AI Assistant Environment Verification")
print("=" * 70)
print(f"\nCurrent directory: {os.getcwd()}")
print(f".env file path: {bot_dir / '.env'}")
print(f".env exists: {(bot_dir / '.env').exists()}")

# Load .env file
load_dotenv(override=True)

print("\n" + "=" * 70)
print("Environment Variables Check")
print("=" * 70)

# Check AI settings
ai_enabled = os.getenv("AI_ENABLED", "true").lower() == "true"
ai_provider = os.getenv("AI_PROVIDER", "groq").lower()

print(f"\nAI_ENABLED: {ai_enabled}")
print(f"AI_PROVIDER: {ai_provider}")

# Check API keys
groq_key = os.getenv("GROQ_API_KEY", "").strip()
openai_key = os.getenv("OPENAI_API_KEY", "").strip()
anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()

print(f"\nAPI Keys:")
print(f"  GROQ_API_KEY: {'[FOUND]' if groq_key else '[NOT FOUND]'}")
if groq_key:
    print(f"    Length: {len(groq_key)} characters")
    print(f"    Preview: {groq_key[:15]}...{groq_key[-5:] if len(groq_key) > 20 else ''}")
    # Check for common issues
    if groq_key.startswith('"') or groq_key.startswith("'"):
        print(f"    WARNING: Key starts with quote - remove quotes!")
    if groq_key.endswith('"') or groq_key.endswith("'"):
        print(f"    WARNING: Key ends with quote - remove quotes!")
    if ' ' in groq_key:
        print(f"    WARNING: Key contains spaces - check for extra characters!")

print(f"  OPENAI_API_KEY: {'[FOUND]' if openai_key else '[Not found]'}")
print(f"  ANTHROPIC_API_KEY: {'[FOUND]' if anthropic_key else '[Not found]'}")

# Determine which key should be used
if ai_provider == "groq":
    key_to_use = groq_key
elif ai_provider == "openai":
    key_to_use = openai_key
elif ai_provider == "anthropic":
    key_to_use = anthropic_key
else:
    key_to_use = groq_key or openai_key or anthropic_key

print("\n" + "=" * 70)
print("Result")
print("=" * 70)

if key_to_use:
    print(f"\n[SUCCESS] API key found for provider '{ai_provider}'")
    print(f"   Key length: {len(key_to_use)} characters")
    print(f"   Key preview: {key_to_use[:15]}...{key_to_use[-5:] if len(key_to_use) > 20 else ''}")
else:
    print(f"\n[ERROR] No API key found for provider '{ai_provider}'")
    print(f"\nTo fix:")
    print(f"   1. Make sure your .env file is in: {bot_dir}")
    print(f"   2. Add this line to .env:")
    if ai_provider == "groq":
        print(f"      GROQ_API_KEY=your-actual-api-key-here")
    elif ai_provider == "openai":
        print(f"      OPENAI_API_KEY=sk-your-actual-api-key-here")
    elif ai_provider == "anthropic":
        print(f"      ANTHROPIC_API_KEY=your-actual-api-key-here")
    print(f"   3. Make sure there are NO spaces around the = sign")
    print(f"   4. Make sure there are NO quotes around the key")
    print(f"   5. Restart the bot: sudo systemctl restart discord-bot")

print("\n" + "=" * 70)
