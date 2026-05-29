"""
Build a single confidence-rated identification from all pre-check signals.

The lookup pipeline returns several partial signals: fingerprints, StashDB,
ThePornDB, filename parsing, media tags, OCR and Ollama. This module turns
those into one user-facing recommendation without hiding the underlying data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path

_STOP_WORDS = {
    "a", "an", "and", "at", "by", "for", "from", "her", "his", "in", "is",
    "my", "of", "on", "or", "the", "to", "with", "your",
}


@dataclass
class Identification:
    status: str = "unknown"          # duplicate | identified | likely | possible | unknown
    confidence: float = 0.0
    source: str = "none"
    reason: str = "Keine eindeutige Identifikation"
    suggested_name: str | None = None
    action: str = "review"           # skip | rename | review
    signals: list[str] | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["signals"] = data["signals"] or []
        return data


def _words(value: str | None) -> set[str]:
    if not value:
        return set()
    cleaned = re.sub(r"[^a-zA-Z0-9]+", " ", value.lower())
    return {w for w in cleaned.split() if len(w) >= 3 and w not in _STOP_WORDS}


def _name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _performer_overlap(left: list[str] | None, right: list[str] | None) -> int:
    left_keys = [_name_key(p) for p in (left or []) if p]
    right_keys = [_name_key(p) for p in (right or []) if p]
    matches = 0
    for lp in left_keys:
        if any(lp and rp and (lp in rp or rp in lp) for rp in right_keys):
            matches += 1
    return matches


def _scene_name(scene: dict) -> str:
    parts = [p for p in [scene.get("site") or scene.get("studio") or scene.get("network"), scene.get("date"), scene.get("title")] if p]
    name = " - ".join(parts)
    performers = scene.get("performers") or []
    if performers:
        name += f" ({', '.join(performers[:3])})"
    return re.sub(r'[<>:"/\\|?*]', "", name).strip()


def _with_ext(name: str | None, ext: str) -> str | None:
    if not name:
        return None
    if Path(name).suffix:
        return name
    return name + ext


def _cross_confirm(stash_scene: dict | None, tpdb_scene: dict | None, parsed: dict | None) -> tuple[bool, list[str]]:
    signals: list[str] = []
    if not stash_scene or not tpdb_scene:
        return False, signals

    title_overlap = _words(stash_scene.get("title")) & _words(tpdb_scene.get("title"))
    if len(title_overlap) >= 2:
        signals.append("Titelüberschneidung StashDB/TPDB")

    perf_matches = _performer_overlap(stash_scene.get("performers"), tpdb_scene.get("performers"))
    if perf_matches:
        signals.append(f"{perf_matches} Performer bestätigt")

    parsed_perfs = (parsed or {}).get("performers") or []
    parsed_matches = _performer_overlap(parsed_perfs, tpdb_scene.get("performers"))
    if parsed_matches >= 2:
        signals.append(f"{parsed_matches} Dateiname-Performer bestätigt")

    if stash_scene.get("date") and stash_scene.get("date") == tpdb_scene.get("date"):
        signals.append("Datum bestätigt")

    return bool(signals), signals


def build_identification(
    *,
    original_name: str,
    stashdb_scenes: list[dict],
    stashdb_suggested: str | None,
    tpdb_scenes: list[dict],
    tpdb_suggested: str | None,
    ollama: dict | None,
    filename_parsed: dict | None,
    dest_duplicate: str | None,
) -> dict:
    ext = Path(original_name).suffix
    signals: list[str] = []

    if dest_duplicate:
        return Identification(
            status="duplicate",
            confidence=1.0,
            source="dest",
            reason="Gleicher oshash existiert bereits in dest/",
            suggested_name=None,
            action="skip",
            signals=["Duplikat erkannt"],
        ).to_dict()

    sdb = stashdb_scenes[0] if stashdb_scenes else None
    tpdb = tpdb_scenes[0] if tpdb_scenes else None

    if sdb and sdb.get("match_via") == "fingerprint":
        return Identification(
            status="identified",
            confidence=0.97,
            source="StashDB Fingerprint",
            reason="StashDB hat die Datei über oshash/phash eindeutig erkannt",
            suggested_name=stashdb_suggested,
            action="rename",
            signals=["Fingerprint-Treffer"],
        ).to_dict()

    if tpdb and (tpdb.get("match_method") == "hash" or not tpdb.get("score")):
        return Identification(
            status="identified",
            confidence=0.96,
            source="ThePornDB Fingerprint",
            reason="ThePornDB hat die Datei über oshash/phash eindeutig erkannt",
            suggested_name=tpdb_suggested or _with_ext(_scene_name(tpdb), ext),
            action="rename",
            signals=["Fingerprint-Treffer"],
        ).to_dict()

    confirmed, confirm_signals = _cross_confirm(sdb, tpdb, filename_parsed)
    if confirmed and tpdb:
        signals.extend(confirm_signals)
        score = int(tpdb.get("score") or 0)
        confidence = 0.90 if score >= 60 else 0.86
        return Identification(
            status="identified",
            confidence=confidence,
            source="StashDB + ThePornDB",
            reason="Mehrere Quellen bestätigen dieselbe Szene",
            suggested_name=tpdb_suggested or stashdb_suggested or _with_ext(_scene_name(tpdb), ext),
            action="rename",
            signals=signals,
        ).to_dict()

    if tpdb:
        score = int(tpdb.get("score") or 0)
        if score >= 80:
            status, confidence, action = "identified", 0.88, "rename"
        elif score >= 50:
            status, confidence, action = "likely", 0.76, "review"
        else:
            status, confidence, action = "possible", 0.58, "review"
        return Identification(
            status=status,
            confidence=confidence,
            source="ThePornDB Kontextsuche",
            reason=f"ThePornDB Kontextscore {score}",
            suggested_name=tpdb_suggested or _with_ext(_scene_name(tpdb), ext),
            action=action,
            signals=[f"TPDB Score {score}"],
        ).to_dict()

    if sdb:
        via = sdb.get("match_via") or "context"
        confidence = 0.78 if via == "context" else 0.64
        return Identification(
            status="likely" if via == "context" else "possible",
            confidence=confidence,
            source=f"StashDB {via}",
            reason="StashDB liefert einen Kontexttreffer, aber keinen Fingerprint-Treffer",
            suggested_name=stashdb_suggested,
            action="review",
            signals=[f"StashDB {via}"],
        ).to_dict()

    if ollama and not ollama.get("error"):
        conf = float(ollama.get("confidence") or 0)
        if conf >= 0.85:
            return Identification(
                status="possible",
                confidence=0.55,
                source="Ollama",
                reason="Nur Dateiname/Ollama, keine Datenbankbestätigung",
                suggested_name=_with_ext(ollama.get("cleaned_name"), ext),
                action="review",
                signals=["LLM-Vorschlag ohne DB-Treffer"],
            ).to_dict()

    return Identification().to_dict()
