"""
Studio and performer abbreviation resolution.

Two alias sources — layered in priority order:

  1. Runtime aliases  — /etc/namer-helper/aliases.json  (writable by the app,
                         populated by learn() after successful TPDB matches)
  2. Package aliases  — src/namer_helper/data/aliases.json  (ships with the
                         wheel, initial studio set, read-only after install)

load(path) reads the file at the given path.  When the file is missing it
falls back to the built-in _DEFAULT_STUDIOS dict — so the app degrades
gracefully on a fresh install before any aliases file exists.

learn() always writes to the runtime path passed by the caller (typically
/etc/namer-helper/aliases.json) so learned aliases survive restarts and
are separate from the package file (which may be overwritten on upgrade).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Built-in paths: ship inside the package wheel.
PACKAGE_ALIASES_PATH: Path = Path(__file__).parent / "data" / "aliases.json"
PACKAGE_PERFORMER_ALIASES_PATH: Path = Path(__file__).parent / "data" / "performer_aliases.json"


# Built-in fallback — used when aliases.json is missing or unreadable.
_DEFAULT_STUDIOS: dict[str, str] = {
    "EA":    "Evil Angel",
    "BRZ":   "Brazzers",
    "NF":    "NubileFilms",
    "DDF":   "DDFNetwork",
    "21S":   "21Sextury",
    "ZTOD":  "ZeroTolerance",
    "MFX":   "MetFX",
    "RK":    "Reality Kings",
    "WF":    "WoodmanCastingX",
    "NXG":   "Naughty America",
    "LES":   "LesbianX",
    "GIO":   "GirlsInOrgasms",
    "VIXEN": "Vixen Media Group",
    "BB":    "Bang Bros",
    "KT":    "Kink.com",
    "FA":    "Fake Agent",
    "GS":    "Girlfriends Films",
    "ZTV":   "Zero Tolerance",
}


@dataclass
class Aliases:
    studios: dict[str, str] = field(default_factory=dict)
    performers: dict[str, str] = field(default_factory=dict)


def merge(base: Aliases, extra: Aliases) -> Aliases:
    """Return a new Aliases combining both, with extra taking priority on conflicts."""
    return Aliases(
        studios={**base.studios, **extra.studios},
        performers={**base.performers, **extra.performers},
    )


def load(path: Path | None = None) -> Aliases:
    """Load aliases from a JSON file. Returns built-in defaults if missing.

    path=None → uses PACKAGE_ALIASES_PATH (the file shipped with the wheel).
    Pass helper_config_dir / "aliases.json" at runtime so learn() and load()
    work from the same writable location.
    """
    if path is None:
        path = PACKAGE_ALIASES_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Aliases(
            studios={k.upper(): v for k, v in data.get("studios", {}).items()},
            performers={k.upper(): v for k, v in data.get("performers", {}).items()},
        )
    except FileNotFoundError:
        return Aliases(studios=dict(_DEFAULT_STUDIOS))
    except Exception:
        return Aliases(studios=dict(_DEFAULT_STUDIOS))


def resolve_studio(token: str, aliases: Aliases) -> str:
    """Return the full studio name for a token, or the token unchanged."""
    return aliases.studios.get(token.upper(), token)


def resolve_performer(token: str, aliases: Aliases) -> str:
    """Return the full performer name for a token, or the token unchanged.

    Designed for abbreviations and initials only — tokens like "JD" that a
    language model cannot resolve on its own.  Full names written with
    separators (e.g. "jane_doe") are NOT looked up here; filename_parser
    normalises those via separator stripping before the LLM sees them.
    """
    return aliases.performers.get(token.upper(), token)


def learn(path: Path, kind: str, key: str, value: str) -> None:
    """
    Persist a new abbreviation → full name mapping to the JSON file.

    kind: "studios" or "performers"
    key:  the abbreviation (stored uppercase)
    value: the full name

    Called after a successful TPDB/StashDB match.  Silently skips if the
    key is already known or if any I/O error occurs.
    """
    if not key or not value:
        return
    key_upper = key.upper()
    try:
        try:
            data: dict = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {
                "_meta": {"version": 1, "source": "manual+auto"},
                "studios": {k: v for k, v in _DEFAULT_STUDIOS.items()},
                "performers": {},
            }

        section = "studios" if kind == "studios" else "performers"
        data.setdefault(section, {})

        if key_upper in data[section]:
            return  # already known — nothing to do

        data[section][key_upper] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
