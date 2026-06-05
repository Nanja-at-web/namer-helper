"""
Review queue (MVP7) — persistent triage worklist for pre-check results.

After a batch scan, each file's identification is enqueued here so the human
can work through a prioritised list across multiple sessions instead of
re-scanning. The queue survives restarts (JSON-persisted).

Core principle ("nothing made up"): batch confirmation is restricted to
DETERMINISTIC matches only — fingerprint, JAV-code, and learned rules
(confidence >= BATCH_CONFIDENCE_THRESHOLD). Context/score/LLM matches
(`likely`, `possible`, `unknown`) are never batch-eligible and always
require an individual human decision.

States:  pending → confirmed | rejected | deferred

Never raises: load() returns [] on any error, writes silently swallow
failures (queue persistence is best-effort, never blocks the pipeline).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path


# Confidence at/above which a match counts as deterministic.
# Fingerprint 0.96/0.97, JAV-code 0.94, Movie-FP 0.96, Rule/duplicate 1.0.
# Context-identified tops out at 0.90 -> cleanly excluded.
BATCH_CONFIDENCE_THRESHOLD = 0.94

# Defence-in-depth: the source string of a deterministic identification must
# contain one of these markers. Guards against a future confidence-tuning
# accident promoting a context match to batch-eligible.
DETERMINISTIC_MARKERS = ("Fingerprint", "JAV-Code", "Rule")

PENDING = "pending"
CONFIRMED = "confirmed"
REJECTED = "rejected"
DEFERRED = "deferred"

_RESOLVED = {CONFIRMED, REJECTED}


@dataclass
class QueueItem:
    name: str                       # file name relative to pre-check dir (primary key)
    status: str = PENDING
    confidence: float = 0.0
    source: str = ""                # identification source string
    ident_status: str = "unknown"   # identification status (identified/likely/...)
    suggested_name: str | None = None
    action: str = "review"          # rename | review | skip
    reason: str = ""
    oshash: str | None = None
    tpdb_id: str | None = None
    created_at: int = 0
    updated_at: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["batch_eligible"] = is_batch_eligible(self)
        return d


def _now() -> int:
    return int(time.time())


def load_queue(path: Path) -> list[QueueItem]:
    """Load queue items from JSON. Returns [] if missing or unparseable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = []
        for raw in data.get("items", []):
            if not isinstance(raw, dict) or not raw.get("name"):
                continue
            items.append(QueueItem(
                name=raw["name"],
                status=raw.get("status", PENDING),
                confidence=float(raw.get("confidence", 0.0)),
                source=raw.get("source", ""),
                ident_status=raw.get("ident_status", "unknown"),
                suggested_name=raw.get("suggested_name"),
                action=raw.get("action", "review"),
                reason=raw.get("reason", ""),
                oshash=raw.get("oshash"),
                tpdb_id=raw.get("tpdb_id"),
                created_at=int(raw.get("created_at", 0)),
                updated_at=int(raw.get("updated_at", 0)),
            ))
        return items
    except Exception:
        return []


def save_queue(path: Path, items: list[QueueItem]) -> bool:
    """Persist queue items. Silently returns False on any I/O error."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "items": [asdict(i) for i in items]}
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return True
    except Exception:
        return False


def is_batch_eligible(item: QueueItem) -> bool:
    """True only for pending, deterministic, rename-ready matches.

    Triple gate: status pending + confidence >= threshold + deterministic
    source marker + action == rename. This is the safety boundary that keeps
    uncertain matches out of stapel-confirmation.
    """
    if item.status != PENDING:
        return False
    if item.action != "rename":
        return False
    if item.confidence < BATCH_CONFIDENCE_THRESHOLD:
        return False
    return any(marker in (item.source or "") for marker in DETERMINISTIC_MARKERS)


def enqueue(
    path: Path,
    *,
    name: str,
    identification: dict | None,
    oshash: str | None = None,
    tpdb_id: str | None = None,
) -> bool:
    """Insert or update a queue item from a lookup identification.

    Keyed by file name. An existing item that the user already resolved
    (confirmed/rejected) is left untouched — a re-scan must not silently
    reopen a decision. Pending/deferred items are refreshed.
    """
    if not name or identification is None:
        return False
    items = load_queue(path)
    now = _now()

    existing = next((i for i in items if i.name == name), None)
    if existing is not None and existing.status in _RESOLVED:
        return False  # don't reopen a settled decision

    new_item = QueueItem(
        name=name,
        status=existing.status if existing else PENDING,
        confidence=float(identification.get("confidence", 0.0)),
        source=identification.get("source", ""),
        ident_status=identification.get("status", "unknown"),
        suggested_name=identification.get("suggested_name"),
        action=identification.get("action", "review"),
        reason=identification.get("reason", ""),
        oshash=oshash or (existing.oshash if existing else None),
        tpdb_id=tpdb_id or (existing.tpdb_id if existing else None),
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
    items = [i for i in items if i.name != name]
    items.append(new_item)
    return save_queue(path, items)


def set_status(path: Path, name: str, status: str) -> bool:
    """Update one item's status. Returns False if the item is absent."""
    if status not in {PENDING, CONFIRMED, REJECTED, DEFERRED}:
        return False
    items = load_queue(path)
    found = False
    for item in items:
        if item.name == name:
            item.status = status
            item.updated_at = _now()
            found = True
            break
    if not found:
        return False
    return save_queue(path, items)


def get_item(path: Path, name: str) -> QueueItem | None:
    for item in load_queue(path):
        if item.name == name:
            return item
    return None


def batch_eligible(items: list[QueueItem]) -> list[QueueItem]:
    """Return the deterministic, pending items safe for stapel-confirmation."""
    return [i for i in items if is_batch_eligible(i)]


# Eligible-source categories, in descending reliability order.
ELIGIBLE_CATEGORIES = ("fingerprint", "jav", "rule")


def eligible_category(item: QueueItem) -> str:
    """Classify a batch-eligible item by its deterministic source.

    fingerprint = oshash/phash match (essentially certain)
    jav         = TPDB JAV-code match (high, but filename-derived)
    rule        = previously user-confirmed (certain by definition)
    """
    s = item.source or ""
    if "Rule" in s:
        return "rule"
    if "JAV-Code" in s:
        return "jav"
    if "Fingerprint" in s:
        return "fingerprint"
    return "fingerprint"  # any other deterministic marker → safest bucket


def batch_eligible_by_category(items: list[QueueItem]) -> dict[str, list[QueueItem]]:
    """Group batch-eligible items into fingerprint / jav / rule buckets."""
    out: dict[str, list[QueueItem]] = {c: [] for c in ELIGIBLE_CATEGORIES}
    for item in batch_eligible(items):
        out[eligible_category(item)].append(item)
    return out


def _sort_key(item: QueueItem) -> tuple:
    # Order: pending-review (needs human) -> pending batch-eligible ->
    #        deferred -> resolved. Within a group, highest confidence first.
    if item.status == PENDING and not is_batch_eligible(item):
        group = 0
    elif item.status == PENDING:
        group = 1
    elif item.status == DEFERRED:
        group = 2
    else:
        group = 3
    return (group, -item.confidence, item.name)


def sort_for_review(items: list[QueueItem]) -> list[QueueItem]:
    """Sort so the items needing a human decision come first."""
    return sorted(items, key=_sort_key)


def summary(items: list[QueueItem]) -> dict:
    """Counts for the dashboard."""
    by_cat = batch_eligible_by_category(items)
    return {
        "total": len(items),
        "pending": sum(1 for i in items if i.status == PENDING),
        "batch_eligible": len(batch_eligible(items)),
        "eligible_fingerprint": len(by_cat["fingerprint"]),
        "eligible_jav": len(by_cat["jav"]),
        "eligible_rule": len(by_cat["rule"]),
        "needs_review": sum(
            1 for i in items if i.status == PENDING and not is_batch_eligible(i)
        ),
        "deferred": sum(1 for i in items if i.status == DEFERRED),
        "confirmed": sum(1 for i in items if i.status == CONFIRMED),
        "rejected": sum(1 for i in items if i.status == REJECTED),
    }


def remove_resolved(path: Path) -> int:
    """Drop confirmed/rejected items. Returns how many were removed."""
    items = load_queue(path)
    kept = [i for i in items if i.status not in _RESOLVED]
    removed = len(items) - len(kept)
    if removed:
        save_queue(path, kept)
    return removed


def clear_all(path: Path) -> int:
    """Remove EVERY queue entry (full reset). Returns how many were removed.

    Used to start a fresh scan from scratch — does NOT touch any files, only
    the queue's decision records.
    """
    count = len(load_queue(path))
    if count:
        save_queue(path, [])
    return count
