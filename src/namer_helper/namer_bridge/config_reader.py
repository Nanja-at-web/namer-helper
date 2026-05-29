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


def read_namer_porndb_token(config_path: Path) -> str:
    """Read porndb_token from namer.cfg — Namer's own TPDB credential."""
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")
    # namer.cfg has porndb_token in the [namer] section (or no section header)
    for section in parser.sections():
        val = parser.get(section, "porndb_token", fallback="")
        if val.strip():
            return val.strip()
    # Also try DEFAULT (keys outside any section)
    return parser.defaults().get("porndb_token", "").strip()
