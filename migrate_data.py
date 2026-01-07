"""Script to migrate existing JSON files to data/ directory."""
import os
import shutil
from pathlib import Path

# Files to migrate
FILES_TO_MIGRATE = [
    "bot_state.json",
    "fitgirl_seen_posts.json",
    "user_data.json",
    "reviews_data.json",
    "tags_data.json",
    "link_health_data.json",
    "webhooks_data.json",
    "collections_data.json",
    "compatibility_data.json",
    "steam_links.json"
]

def migrate_files():
    """Migrate JSON files to data/ directory."""
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    
    migrated = []
    skipped = []
    
    for filename in FILES_TO_MIGRATE:
        src = Path(filename)
        dst = data_dir / filename
        
        if src.exists():
            if dst.exists():
                print(f"⚠️  {filename} already exists in data/, skipping")
                skipped.append(filename)
            else:
                shutil.move(str(src), str(dst))
                print(f"✅ Migrated {filename} to data/")
                migrated.append(filename)
        else:
            print(f"Info: {filename} not found, skipping")
    
    print(f"\nMigration complete!")
    print(f"   Migrated: {len(migrated)} files")
    print(f"   Skipped: {len(skipped)} files")
    
    if migrated:
        print(f"\nMigrated files: {', '.join(migrated)}")

if __name__ == "__main__":
    migrate_files()

