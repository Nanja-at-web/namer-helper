"""
Self-learning vocabulary of known studios/sites and performers.

Populated automatically from successful StashDB/TPDB lookups (those return
real database entities), so the set grows to cover exactly the user's library.
Used by filename_parser to stop misclassifying a studio name as a performer
(e.g. "ATKGalleria", "5K Porn", "Evil Angel") and vice versa.

Storage (runtime, writable):
  <config_dir>/known_studios.json
  <config_dir>/known_performers.json

Each file: {"names": {<normalized_key>: <display_name>}}

Never raises: load() returns an empty Vocabulary on any error, learn() is
best-effort and silently swallows write failures.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_STUDIOS_FILE = "known_studios.json"
_PERFORMERS_FILE = "known_performers.json"


def _key(name: str) -> str:
    """Normalize a name for matching: lowercase, alphanumeric only."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


@dataclass
class Vocabulary:
    studios: dict[str, str] = field(default_factory=dict)      # key -> display
    performers: dict[str, str] = field(default_factory=dict)

    def is_studio(self, name: str) -> bool:
        return _key(name) in self.studios

    def is_performer(self, name: str) -> bool:
        return _key(name) in self.performers

    def studio_display(self, name: str) -> str | None:
        return self.studios.get(_key(name))


def _load_file(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        names = data.get("names", {})
        return {str(k): str(v) for k, v in names.items() if k}
    except Exception:
        return {}


def load(config_dir: Path) -> Vocabulary:
    return Vocabulary(
        studios=_load_file(config_dir / _STUDIOS_FILE),
        performers=_load_file(config_dir / _PERFORMERS_FILE),
    )


def _save_file(path: Path, names: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"names": names}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def learn(config_dir: Path, *, studios: list[str] | None = None,
          performers: list[str] | None = None) -> int:
    """Add studio/performer names to the vocabulary. Returns how many were new.

    Best-effort: a display name is kept under its normalized key. Existing
    keys are not overwritten (first-seen display wins). Empty/too-short names
    (< 2 alnum chars) are ignored.
    """
    new = 0
    try:
        for filename, incoming in ((_STUDIOS_FILE, studios), (_PERFORMERS_FILE, performers)):
            if not incoming:
                continue
            path = config_dir / filename
            current = _load_file(path)
            changed = False
            for raw in incoming:
                k = _key(raw)
                if len(k) < 2 or k in current:
                    continue
                current[k] = (raw or "").strip()
                changed = True
                new += 1
            if changed:
                _save_file(path, current)
    except Exception:
        pass
    return new
