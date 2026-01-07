#!/bin/bash
# Script to fix git merge conflict on Oracle Cloud server
# Run this on your Oracle Cloud server

set -e

echo "🔧 Fixing git merge conflict..."

# Create data directory if it doesn't exist
mkdir -p data

# Move conflicting files to data/ directory
if [ -f "bot_state.json" ]; then
    echo "Moving bot_state.json to data/"
    mv bot_state.json data/bot_state.json
fi

if [ -f "fitgirl_seen_posts.json" ]; then
    echo "Moving fitgirl_seen_posts.json to data/"
    mv fitgirl_seen_posts.json data/fitgirl_seen_posts.json
fi

# Move any other JSON files that might be in root
for file in *.json; do
    if [ -f "$file" ] && [ "$file" != "package.json" ]; then
        echo "Moving $file to data/"
        mv "$file" data/
    fi
done

# Create logs directory
mkdir -p logs

echo "✅ Files moved to data/ directory"
echo ""
echo "Now you can:"
echo "1. git pull origin main"
echo "2. Or if still having issues: git stash, then git pull, then git stash pop"

