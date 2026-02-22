"""
Quick script to check AI configuration
Run this to verify your .env file is set up correctly
"""

import os
from dotenv import load_dotenv

load_dotenv()

print("=" * 60)
print("AI Assistant Configuration Check")
print("=" * 60)

# Check AI_ENABLED
ai_enabled = os.getenv("AI_ENABLED", "true").lower() == "true"
print(f"\n✅ AI_ENABLED: {ai_enabled}")

# Check provider
provider = os.getenv("AI_PROVIDER", "groq").lower()
print(f"✅ AI_PROVIDER: {provider}")

# Check API keys
groq_key = os.getenv("GROQ_API_KEY", "")
openai_key = os.getenv("OPENAI_API_KEY", "")
anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

print(f"\n📋 API Keys Found:")
print(f"  GROQ_API_KEY: {'✅ Found' if groq_key else '❌ Not found'} ({len(groq_key)} chars)")
print(f"  OPENAI_API_KEY: {'✅ Found' if openai_key else '❌ Not found'} ({len(openai_key)} chars)")
print(f"  ANTHROPIC_API_KEY: {'✅ Found' if anthropic_key else '❌ Not found'} ({len(anthropic_key)} chars)")

# Determine which key to use
if provider == "groq":
    key_to_use = groq_key
elif provider == "openai":
    key_to_use = openai_key
elif provider == "anthropic":
    key_to_use = anthropic_key
else:
    key_to_use = groq_key or openai_key or anthropic_key

print(f"\n🔑 Key for {provider}: {'✅ Found' if key_to_use else '❌ NOT FOUND'}")

if key_to_use:
    print(f"   Key preview: {key_to_use[:10]}...{key_to_use[-4:] if len(key_to_use) > 14 else '***'}")
else:
    print("\n❌ ERROR: No API key found for the selected provider!")
    print(f"\n💡 To fix:")
    if provider == "groq":
        print("   Add to .env: GROQ_API_KEY=your-key-here")
    elif provider == "openai":
        print("   Add to .env: OPENAI_API_KEY=sk-your-key-here")
    elif provider == "anthropic":
        print("   Add to .env: ANTHROPIC_API_KEY=your-key-here")

print("\n" + "=" * 60)
