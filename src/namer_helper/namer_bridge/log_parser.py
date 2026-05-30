"""
Parses namer failed-log files (.namer_failed.log) and watchdog logs.

Each FailedMatch is classified with a FailureReason that explains why
Namer could not match the file.  The classification uses the parsed log
data plus normalize() to detect leet/noise in the filename.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class FailureReason(Enum):
    NO_DATE          = "A"  # no date found in filename / log
    UNKNOWN_STUDIO   = "B"  # site_hint absent — studio not identifiable
    PERFORMER_ABBREV = "C"  # performer abbreviation could not be resolved
    LOW_FUZZY_SCORE  = "D"  # match_score < 0.95 (best candidate below threshold)
    ZERO_API_RESULTS = "E"  # no candidates returned by TPDB (no score in log)
    MULTIPLE_MATCHES = "F"  # multiple equal-score candidates, no winner chosen
    LEET_NOT_PARSED  = "H"  # leet-speak detected in filename
    NOISE_CHARS      = "I"  # noise characters (#, *, brackets) in filename
    UNKNOWN          = "Z"  # could not determine reason


_NAMER_THRESHOLD = 0.95   # RapidFuzz similarity threshold Namer uses

_FAILED_LOG_SUFFIX = ".namer_failed.log"
_MATCH_SCORE_RE = re.compile(r"match score[:\s]+([0-9.]+)", re.IGNORECASE)
_SITE_RE = re.compile(r"site[:\s]+([^\n,]+)", re.IGNORECASE)
_DATE_RE = re.compile(r"date[:\s]+(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
# Matches: Calculated hashes: {'duration': 123, 'phash': 'abc...', 'oshash': 'def...'}
_PHASH_RE = re.compile(r"['\"]?phash['\"]?\s*:\s*['\"]?([a-fA-F0-9]+)['\"]?")
_OSHASH_RE = re.compile(r"['\"]?oshash['\"]?\s*:\s*['\"]?([a-fA-F0-9]+)['\"]?")
_DURATION_RE = re.compile(r"['\"]?duration['\"]?\s*:\s*([0-9]+)")


@dataclass
class FailedMatch:
    file_path: Path
    log_path: Path
    match_score: float | None = None
    site_hint: str | None = None
    date_hint: str | None = None
    phash: str | None = None
    oshash: str | None = None
    duration: int | None = None
    failure_reason: FailureReason = FailureReason.UNKNOWN
    raw_log: str = field(default="", repr=False)


def classify_failure(match: "FailedMatch") -> FailureReason:
    """Determine the most likely reason Namer failed to match this file.

    Priority order: leet/noise (fixable by normalize) → score signals
    (API results, threshold) → structural gaps (date, studio) → unknown.
    """
    from namer_helper.normalize import normalize
    norm = normalize(match.file_path.name)

    if norm.leet_detected:
        return FailureReason.LEET_NOT_PARSED
    if norm.noise_detected:
        return FailureReason.NOISE_CHARS
    if match.match_score is None:
        # No score in log → TPDB returned zero candidates
        return FailureReason.ZERO_API_RESULTS
    if match.match_score >= _NAMER_THRESHOLD:
        # Score passed the threshold but file is still in failed — multiple matches
        return FailureReason.MULTIPLE_MATCHES
    if match.match_score < _NAMER_THRESHOLD:
        return FailureReason.LOW_FUZZY_SCORE
    if match.date_hint is None:
        return FailureReason.NO_DATE
    if match.site_hint is None:
        return FailureReason.UNKNOWN_STUDIO
    return FailureReason.UNKNOWN


def find_failed_logs(failed_dir: Path) -> Iterator[Path]:
    """Yield all .namer_failed.log files under failed_dir."""
    yield from failed_dir.rglob(f"*{_FAILED_LOG_SUFFIX}")


def parse_failed_log(log_path: Path) -> FailedMatch:
    """Parse a single .namer_failed.log file into a FailedMatch."""
    raw = log_path.read_text(encoding="utf-8", errors="replace")

    # Corresponding media file: same name without the log suffix
    stem = log_path.name[: -len(_FAILED_LOG_SUFFIX)]
    media_path = log_path.with_name(stem)

    score_match = _MATCH_SCORE_RE.search(raw)
    site_match = _SITE_RE.search(raw)
    date_match = _DATE_RE.search(raw)
    phash_match = _PHASH_RE.search(raw)
    oshash_match = _OSHASH_RE.search(raw)
    duration_match = _DURATION_RE.search(raw)

    result = FailedMatch(
        file_path=media_path,
        log_path=log_path,
        match_score=float(score_match.group(1)) if score_match else None,
        site_hint=site_match.group(1).strip() if site_match else None,
        date_hint=date_match.group(1) if date_match else None,
        phash=phash_match.group(1) if phash_match else None,
        oshash=oshash_match.group(1) if oshash_match else None,
        duration=int(duration_match.group(1)) if duration_match else None,
        raw_log=raw,
    )
    result.failure_reason = classify_failure(result)
    return result


def collect_failed_matches(failed_dir: Path) -> list[FailedMatch]:
    """Return all FailedMatch entries found under failed_dir."""
    results = []
    for log_path in find_failed_logs(failed_dir):
        try:
            results.append(parse_failed_log(log_path))
        except OSError:
            pass
    results.sort(key=lambda m: m.file_path.name)
    return results
