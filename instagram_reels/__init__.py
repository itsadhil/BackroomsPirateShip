"""
Bridge to Instagram-Reels-Scraper-Auto-Poster. Injects configurable paths and
exposes config + run functions for use by the Discord bot.
"""
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

# Base dir for DB and downloads (set on first init)
_base_dir: Optional[str] = None
_initialized = False

# Lazy-loaded refs after init
_ig_config = None
_ig_db = None
_ig_helpers = None
_ig_reels = None
_ig_poster = None
_ig_shorts = None
_ig_remover = None
_ig_auth = None


def _src_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "Instagram-Reels-Scraper-Auto-Poster-2.2" / "src"


def init(base_dir: str) -> None:
    """Call once before any other function. Sets DB and download paths and loads Instagram modules."""
    global _base_dir, _initialized, _ig_config, _ig_db, _ig_helpers, _ig_reels, _ig_poster, _ig_shorts, _ig_remover, _ig_auth
    if _initialized:
        return
    base_path = Path(base_dir).resolve()
    base_path.mkdir(parents=True, exist_ok=True)
    (base_path / "downloads").mkdir(exist_ok=True)
    db_path = str(base_path / "sqlite.db")
    download_dir = str(base_path / "downloads") + os.sep

    src = _src_dir()
    if not src.exists():
        raise FileNotFoundError(f"Instagram Reels src not found: {src}")
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    import config as ig_config
    _ig_config = ig_config
    ig_config.DB_PATH = db_path
    ig_config.DOWNLOAD_DIR = download_dir
    ig_config.CURRENT_DIR = str(src) + os.sep

    import db as ig_db
    _ig_db = ig_db
    _seed_defaults(ig_db)
    import helpers as ig_helpers
    _ig_helpers = ig_helpers
    import reels as ig_reels
    _ig_reels = ig_reels
    import poster as ig_poster
    _ig_poster = ig_poster
    import shorts as ig_shorts
    _ig_shorts = ig_shorts
    import remover as ig_remover
    _ig_remover = ig_remover
    import auth as ig_auth
    _ig_auth = ig_auth

    _base_dir = base_dir
    _initialized = True


def _seed_defaults(ig_db) -> None:
    Session = ig_db.Session
    Config = ig_db.Config
    defaults = [
        ("IS_REMOVE_FILES", "1"),
        ("REMOVE_FILE_AFTER_MINS", "120"),
        ("IS_ENABLED_REELS_SCRAPER", "1"),
        ("IS_ENABLED_AUTO_POSTER", "1"),
        ("IS_POST_TO_STORY", "1"),
        ("FETCH_LIMIT", "10"),
        ("POSTING_INTERVAL_IN_MIN", "15"),
        ("SCRAPER_INTERVAL_IN_MIN", "720"),
        ("USERNAME", ""),
        ("PASSWORD", ""),
        ("ACCOUNTS", ""),
        ("HASTAGS", "#reels #shorts #likes #follow"),
        ("LIKE_AND_VIEW_COUNTS_DISABLED", "0"),
        ("DISABLE_COMMENTS", "0"),
        ("IS_ENABLED_YOUTUBE_SCRAPING", "0"),
        ("YOUTUBE_API_KEY", ""),
        ("CHANNEL_LINKS", ""),
    ]
    session = Session()
    try:
        for key, value in defaults:
            if session.query(Config).filter_by(key=key).first() is None:
                session.add(Config(key=key, value=value, created_at=datetime.now(), updated_at=datetime.now()))
        session.commit()
    finally:
        session.close()


def ensure_initialized() -> None:
    if not _initialized:
        raise RuntimeError("instagram_reels.init(base_dir) must be called first")


def get_config(key: str, default: Optional[str] = None) -> Optional[str]:
    ensure_initialized()
    session = _ig_db.Session()
    try:
        row = session.query(_ig_db.Config).filter_by(key=key).first()
        return row.value if row else default
    finally:
        session.close()


def set_config(key: str, value: str) -> None:
    ensure_initialized()
    _ig_helpers.save_config(key, value)


def load_all_config() -> None:
    ensure_initialized()
    _ig_helpers.load_all_config()


def get_all_config() -> Dict[str, str]:
    ensure_initialized()
    session = _ig_db.Session()
    try:
        rows = session.query(_ig_db.Config).all()
        out = {}
        for r in rows:
            out[r.key] = r.value
        return out
    finally:
        session.close()


def login(username: Optional[str] = None, password: Optional[str] = None):
    """Log in to Instagram. Uses provided credentials or those in config/env."""
    ensure_initialized()
    if username is not None:
        _ig_config.USERNAME = username
    if password is not None:
        _ig_config.PASSWORD = password
    load_all_config()
    return _ig_auth.login()


def run_reels_scrape(api) -> None:
    ensure_initialized()
    load_all_config()
    _ig_reels.main(api)


def run_poster(api) -> None:
    ensure_initialized()
    load_all_config()
    _ig_poster.main(api)


def run_remover() -> None:
    ensure_initialized()
    load_all_config()
    _ig_remover.main()


def run_shorts() -> None:
    ensure_initialized()
    load_all_config()
    _ig_shorts.main()


def get_dashboard_data() -> Dict[str, Any]:
    ensure_initialized()
    session = _ig_db.Session()
    try:
        reels = session.query(_ig_db.Reel).all()
        total = len(reels)
        posted = sum(1 for r in reels if r.is_posted)
        pending = total - posted
        latest = session.query(_ig_db.Reel).order_by(_ig_db.Reel.id.desc()).limit(10).all()
        return {
            "total": total,
            "posted": posted,
            "pending": pending,
            "latest": [(r.id, r.post_id, r.account, r.code, r.is_posted, r.posted_at) for r in latest],
        }
    finally:
        session.close()


def is_initialized() -> bool:
    return _initialized
