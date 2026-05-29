"""
Simple on-disk lookup cache keyed by oshash.

Results are stored as JSON files in CACHE_DIR.
Cache never expires — same file content (same oshash) always yields the same
metadata from StashDB/TPDB. Call invalidate() to force a fresh lookup.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

CACHE_DIR = Path("/opt/namer-helper/lookup-cache")

# Bump when the lookup pipeline changes significantly.
# Any entry with a different version is discarded and re-fetched.
CACHE_VERSION = 12

_REQUIRED_SCENE_KEYS = {"image"}  # bump this set when scene schema changes


def get(oshash: str) -> dict | None:
    f = CACHE_DIR / f"{oshash}.json"
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text())
        # Version check — stale entry → delete and force re-fetch
        if data.get("cache_version") != CACHE_VERSION:
            f.unlink(missing_ok=True)
            return None
        # Schema check: tpdb_scenes must have image field
        scenes = data.get("tpdb_scenes") or []
        if scenes and not _REQUIRED_SCENE_KEYS.issubset(scenes[0].keys()):
            f.unlink(missing_ok=True)
            return None
        data["cached"] = True
        return data
    except Exception:
        return None


def set(oshash: str, result: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        entry = dict(result)
        entry["cache_version"] = CACHE_VERSION
        entry["cached_at"] = int(time.time())
        entry.pop("cached", None)
        (CACHE_DIR / f"{oshash}.json").write_text(json.dumps(entry))
    except Exception:
        pass


def invalidate(oshash: str) -> bool:
    f = CACHE_DIR / f"{oshash}.json"
    if f.exists():
        f.unlink()
        return True
    return False


def stats() -> dict:
    try:
        files = list(CACHE_DIR.glob("*.json"))
        return {"count": len(files), "dir": str(CACHE_DIR)}
    except Exception:
        return {"count": 0, "dir": str(CACHE_DIR)}
