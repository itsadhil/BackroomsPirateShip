"""Safe JSON file storage with locking and atomic writes."""
import json
import os
import sys
import asyncio
from pathlib import Path
from typing import Any, Dict, Optional
from threading import Lock
import logging

logger = logging.getLogger(__name__)

# Try to import fcntl (Unix only)
try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False
    # Windows alternative: use msvcrt for file locking
    try:
        import msvcrt
        HAS_MSVCRT = True
    except ImportError:
        HAS_MSVCRT = False

# Note: On Windows, file locking is less reliable, so we rely more on thread locks

# Global lock for file operations
_file_locks: Dict[str, Lock] = {}
_locks_lock = Lock()

def _get_lock(filename: str) -> Lock:
    """Get or create a lock for a specific file."""
    with _locks_lock:
        if filename not in _file_locks:
            _file_locks[filename] = Lock()
        return _file_locks[filename]

def _lock_file(file_obj, exclusive: bool = False):
    """Lock a file (cross-platform)."""
    if HAS_FCNTL:
        # Unix/Linux - use fcntl
        flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        try:
            fcntl.flock(file_obj.fileno(), flags | fcntl.LOCK_NB)
        except (IOError, OSError):
            # Lock failed, wait and retry
            fcntl.flock(file_obj.fileno(), flags)
    # Windows: msvcrt.locking is not reliable for our use case
    # We rely on thread locks which are sufficient for single-process scenarios
    # For multi-process, consider using a proper database

def _unlock_file(file_obj):
    """Unlock a file (cross-platform)."""
    if HAS_FCNTL:
        try:
            fcntl.flock(file_obj.fileno(), fcntl.LOCK_UN)
        except:
            pass
    # Windows: no-op, thread locks handle it

def load_json(filename: str, default: Any = None) -> Any:
    """Safely load JSON file with error handling."""
    filepath = Path(filename)
    lock = _get_lock(str(filepath.absolute()))
    
    try:
        with lock:
            if not filepath.exists():
                logger.debug(f"File {filename} does not exist, returning default")
                return default if default is not None else {}
            
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    _lock_file(f, exclusive=False)
                    try:
                        data = json.load(f)
                        logger.debug(f"Loaded {filename} successfully")
                        return data
                    finally:
                        _unlock_file(f)
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error in {filename}: {e}")
                # Try to load backup if exists
                backup_path = filepath.with_suffix('.json.bak')
                if backup_path.exists():
                    logger.warning(f"Attempting to load backup: {backup_path}")
                    with open(backup_path, 'r', encoding='utf-8') as f:
                        return json.load(f)
                return default if default is not None else {}
            except Exception as e:
                logger.error(f"Error loading {filename}: {e}", exc_info=True)
                return default if default is not None else {}
    except Exception as e:
        logger.error(f"Critical error loading {filename}: {e}", exc_info=True)
        return default if default is not None else {}

def save_json(data: Any, filename: str, indent: int = 2) -> bool:
    """Safely save JSON file with atomic write and locking."""
    filepath = Path(filename)
    lock = _get_lock(str(filepath.absolute()))
    temp_path = filepath.with_suffix('.json.tmp')
    backup_path = filepath.with_suffix('.json.bak')
    
    try:
        with lock:
            # Create backup of existing file
            if filepath.exists():
                try:
                    import shutil
                    shutil.copy2(filepath, backup_path)
                except Exception as e:
                    logger.warning(f"Could not create backup: {e}")
            
            # Write to temporary file first
            with open(temp_path, 'w', encoding='utf-8') as f:
                # Acquire exclusive lock
                _lock_file(f, exclusive=True)
                try:
                    json.dump(data, f, indent=indent, ensure_ascii=False)
                    f.flush()
                    if hasattr(os, 'fsync'):
                        os.fsync(f.fileno())  # Force write to disk (Unix)
                    else:
                        f.flush()  # Windows alternative
                finally:
                    _unlock_file(f)
            
            # Atomic rename (works on most systems)
            temp_path.replace(filepath)
            logger.debug(f"Saved {filename} successfully")
            return True
            
    except Exception as e:
        logger.error(f"Error saving {filename}: {e}", exc_info=True)
        # Clean up temp file on error
        if temp_path.exists():
            try:
                temp_path.unlink()
            except:
                pass
        return False

async def load_json_async(filename: str, default: Any = None) -> Any:
    """Async wrapper for load_json."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, load_json, filename, default)

async def save_json_async(data: Any, filename: str, indent: int = 2) -> bool:
    """Async wrapper for save_json."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, save_json, data, filename, indent)

