"""
Reads relevant paths from namer.cfg without depending on namer itself.
"""

from __future__ import annotations

import configparser
from pathlib import Path


_DEFAULTS = {
    "failed_dir": "/var/lib/namer/failed",
    "work_dir": "/var/lib/namer/work",
    "watch_dir": "/var/lib/namer/watch",
    "dest_dir": "/var/lib/namer/dest",
}


def read_namer_paths(config_path: Path) -> dict[str, Path]:
    """Return watchdog path values from namer.cfg."""
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")

    paths: dict[str, Path] = {}
    for key, default in _DEFAULTS.items():
        raw = parser.get("watchdog", key, fallback=default)
        paths[key] = Path(raw.strip())

    return paths
