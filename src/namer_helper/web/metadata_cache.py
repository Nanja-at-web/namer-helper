"""
Small on-disk cache for stable file metadata used by the web UI.

Entries are keyed by a hash of the resolved path and validated with size and
mtime_ns, so replacing or editing a file automatically invalidates stale data.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

CACHE_DIR = Path("/opt/namer-helper/metadata-cache")
CACHE_VERSION = 1


def _cache_path(path: Path) -> Path:
    key = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{key}.json"


def _signature(path: Path) -> dict[str, int]:
    st = path.stat()
    return {"size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}


def get(path: Path) -> dict[str, Any] | None:
    try:
        f = _cache_path(path)
        if not f.exists():
            return None
        data = json.loads(f.read_text(encoding="utf-8"))
        if data.get("cache_version") != CACHE_VERSION:
            f.unlink(missing_ok=True)
            return None
        sig = _signature(path)
        if data.get("size") != sig["size"] or data.get("mtime_ns") != sig["mtime_ns"]:
            f.unlink(missing_ok=True)
            return None
        return data.get("metadata") or None
    except Exception:
        return None


def set(path: Path, metadata: dict[str, Any]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        sig = _signature(path)
        entry = {
            "cache_version": CACHE_VERSION,
            "cached_at": int(time.time()),
            **sig,
            "metadata": dict(metadata),
        }
        _cache_path(path).write_text(json.dumps(entry), encoding="utf-8")
    except Exception:
        pass


def invalidate(path: Path) -> bool:
    try:
        f = _cache_path(path)
        if f.exists():
            f.unlink()
            return True
    except Exception:
        pass
    return False
