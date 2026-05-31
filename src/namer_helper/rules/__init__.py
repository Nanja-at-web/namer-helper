"""
Rule learning: persist confirmed rename decisions so the same file is never
re-queried against TPDB/StashDB.

Rules are stored in YAML at:
  runtime:  /etc/namer-helper/rules.yaml       (writable, populated by the app)
  package:  src/namer_helper/data/rules.yaml   (ships empty, read-only)

A rule is created when a user confirms a rename in the pre-check UI.
On subsequent lookups the oshash is matched first (before API calls):
a confirmed match returns immediately with confidence 1.0.

Rule types:
  hash — exact oshash match (deterministic, no false positives)
         suitable for: "this exact file → this exact name"

Never raises: load() returns an empty list on any error, learn() silently
swallows write failures (rule learning is best-effort).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


PACKAGE_RULES_PATH: Path = Path(__file__).parent.parent / "data" / "rules.yaml"

_OSHASH_RE = re.compile(r"^[0-9a-f]{16}$")


@dataclass
class Rule:
    type: str                           # "hash" (only type currently)
    suggested_name: str                 # confirmed rename target (with extension)
    confidence: float = 1.0
    source: str = "user_confirmed"
    oshash: str | None = None           # for type="hash"
    tpdb_id: str | None = None
    created: str | None = None          # YYYY-MM-DD


def load_rules(path: Path | None = None) -> list[Rule]:
    """Load rules from a YAML file. Returns empty list if missing or unparseable."""
    if not _HAS_YAML:
        return []
    if path is None:
        path = PACKAGE_RULES_PATH
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw_rules = data.get("rules") or []
        rules = []
        for r in raw_rules:
            if not isinstance(r, dict):
                continue
            rtype = r.get("type", "hash")
            suggested = r.get("suggested_name", "")
            if not suggested:
                continue
            rules.append(Rule(
                type=rtype,
                suggested_name=suggested,
                confidence=float(r.get("confidence", 1.0)),
                source=r.get("source", "user_confirmed"),
                oshash=r.get("oshash"),
                tpdb_id=r.get("tpdb_id"),
                created=r.get("created"),
            ))
        return rules
    except Exception:
        return []


def match_by_hash(oshash: str, rules: list[Rule]) -> Rule | None:
    """Return the first hash rule matching oshash, or None."""
    if not oshash or not _OSHASH_RE.match(oshash):
        return None
    for rule in rules:
        if rule.type == "hash" and rule.oshash == oshash:
            return rule
    return None


def learn_rule(
    path: Path,
    *,
    oshash: str,
    suggested_name: str,
    tpdb_id: str | None = None,
    source: str = "user_confirmed",
) -> bool:
    """
    Persist a new hash rule to the YAML file.

    Returns True if the rule was written, False if skipped (already known,
    invalid oshash, missing yaml module, or any I/O error).
    """
    if not _HAS_YAML:
        return False
    if not oshash or not _OSHASH_RE.match(oshash):
        return False
    if not suggested_name:
        return False

    try:
        try:
            data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            data = {"version": 1, "rules": []}

        rules: list[dict] = data.setdefault("rules", [])

        # Skip if this oshash is already known
        if any(r.get("oshash") == oshash for r in rules if isinstance(r, dict)):
            return False

        from datetime import date
        rules.append({
            "type": "hash",
            "oshash": oshash,
            "suggested_name": suggested_name,
            "confidence": 1.0,
            "source": source,
            "tpdb_id": tpdb_id,
            "created": date.today().isoformat(),
        })

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        return True

    except Exception:
        return False


def rule_to_identification(rule: Rule, original_name: str) -> dict:
    """Convert a matching rule into the identification dict format."""
    return {
        "status": "identified",
        "confidence": rule.confidence,
        "source": f"Rule ({rule.source})",
        "reason": "Bestätigte Entscheidung aus Rule-Learning",
        "suggested_name": rule.suggested_name,
        "action": "rename",
        "signals": [
            f"Regel: {rule.source}",
            *([ f"TPDB-ID: {rule.tpdb_id}" ] if rule.tpdb_id else []),
        ],
    }
