"""
Studio and performer abbreviation resolution.

Loads alias mappings from data/aliases.json and resolves short codes
found in parsed filenames (EA → Evil Angel, BRZ → Brazzers, …).

Self-learning: after a successful TPDB/StashDB match call learn() to
persist newly discovered abbreviations — keys are stored uppercase so
lookups are always case-insensitive.

Never raises: load() falls back to built-in defaults on any I/O error,
learn() silently swallows write errors (alias learning is best-effort).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


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


def load(path: Path) -> Aliases:
    """Load aliases from a JSON file. Returns built-in defaults if missing."""
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
    """Return the full performer name for a token, or the token unchanged."""
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
