"""
Parses namer failed-log files (.namer_failed.log) and watchdog logs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


_FAILED_LOG_SUFFIX = ".namer_failed.log"
_MATCH_SCORE_RE = re.compile(r"match score[:\s]+([0-9.]+)", re.IGNORECASE)
_SITE_RE = re.compile(r"site[:\s]+([^\n,]+)", re.IGNORECASE)
_DATE_RE = re.compile(r"date[:\s]+(\d{4}-\d{2}-\d{2})", re.IGNORECASE)


@dataclass
class FailedMatch:
    file_path: Path
    log_path: Path
    match_score: float | None = None
    site_hint: str | None = None
    date_hint: str | None = None
    raw_log: str = field(default="", repr=False)


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

    return FailedMatch(
        file_path=media_path,
        log_path=log_path,
        match_score=float(score_match.group(1)) if score_match else None,
        site_hint=site_match.group(1).strip() if site_match else None,
        date_hint=date_match.group(1) if date_match else None,
        raw_log=raw,
    )


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
