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
CACHE_VERSION = 17

_REQUIRED_SCENE_KEYS = {"image"}  # bump this set when scene schema changes
_TIMEOUT_MARKERS = ("timeout", "timed out", "read timed out", "connect timeout")


def get(oshash: str) -> dict | None:
    f = CACHE_DIR / f"{oshash}.json"
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        # Version check — stale entry → delete and force re-fetch
        if data.get("cache_version") != CACHE_VERSION:
            f.unlink(missing_ok=True)
            return None
        # Schema check: tpdb_scenes must have image field
        scenes = data.get("tpdb_scenes") or []
        if scenes and not _REQUIRED_SCENE_KEYS.issubset(scenes[0].keys()):
            f.unlink(missing_ok=True)
            return None
        if is_transient_failure(data):
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
        (CACHE_DIR / f"{oshash}.json").write_text(json.dumps(entry), encoding="utf-8")
    except Exception:
        pass


def is_transient_failure(result: dict) -> bool:
    """Return True for timeout-only/degraded lookup results.

    Stable "no match" entries may be cached, but network timeouts should be
    retried later instead of becoming sticky cache results.
    """
    if _has_positive_result(result):
        return False
    ident = result.get("identification") or {}
    transient_texts = [
        result.get("error"),
        ident.get("source"),
        ident.get("reason"),
        result.get("stashdb_error"),
        result.get("tpdb_error"),
        result.get("tpdb_movie_error"),
    ]
    ollama = result.get("ollama")
    if isinstance(ollama, dict):
        transient_texts.append(ollama.get("error"))
    return any(_has_timeout_marker(value) for value in transient_texts)


def _has_positive_result(result: dict) -> bool:
    ident = result.get("identification") or {}
    if ident.get("action") == "rename" or ident.get("suggested_name"):
        return True
    for key in ("stashdb_scenes", "tpdb_scenes", "tpdb_movies"):
        if result.get(key):
            return True
    return bool(result.get("stashdb_suggested") or result.get("tpdb_suggested") or result.get("tpdb_movie_suggested"))


def _has_timeout_marker(value: object) -> bool:
    text = str(value or "").lower()
    return any(marker in text for marker in _TIMEOUT_MARKERS)


def invalidate(oshash: str) -> bool:
    f = CACHE_DIR / f"{oshash}.json"
    if f.exists():
        f.unlink()
        return True
    return False


def clear_all() -> int:
    """Delete every cached lookup result. Returns how many were removed.

    Used to force re-analysis of all files with updated identification logic
    (the cache never expires, so a logic fix only applies after a clear).
    """
    removed = 0
    try:
        for f in CACHE_DIR.glob("*.json"):
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    except Exception:
        pass
    return removed


def stats() -> dict:
    try:
        files = list(CACHE_DIR.glob("*.json"))
        return {"count": len(files), "dir": str(CACHE_DIR)}
    except Exception:
        return {"count": 0, "dir": str(CACHE_DIR)}
