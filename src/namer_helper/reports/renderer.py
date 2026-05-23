"""
Renders FailedMatch lists to Markdown and JSON reports.
Supports anonymization: filenames are replaced by stable pseudonyms so reports
can be shared for debugging without revealing private file names.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace as dc_replace
from datetime import datetime, timezone
from pathlib import Path

from namer_helper.namer_bridge.log_parser import FailedMatch


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pseudonym(filename: str) -> str:
    """Return a stable 8-char pseudonym for a filename, preserving the extension."""
    h = hashlib.sha256(filename.encode()).hexdigest()[:8]
    suffix = Path(filename).suffix
    return f"[redacted_{h}]{suffix}"


def _anonymize_match(match: FailedMatch) -> FailedMatch:
    """Return a copy of match with file_path and log_path anonymized."""
    pseudo = _pseudonym(match.file_path.name)
    pseudo_log = pseudo + ".namer_failed.log"
    return dc_replace(
        match,
        file_path=match.file_path.with_name(pseudo),
        log_path=match.log_path.with_name(pseudo_log),
        raw_log="",
    )


def to_dict(match: FailedMatch, anonymized: bool = False) -> dict:
    return {
        "file": str(match.file_path),
        "log": str(match.log_path),
        "match_score": match.match_score,
        "site_hint": match.site_hint,
        "date_hint": match.date_hint,
        "file_exists": match.file_path.exists() if not anonymized else None,
    }


def render_json(matches: list[FailedMatch], output_path: Path, anonymized: bool = False) -> None:
    payload = {
        "generated": _now_iso(),
        "anonymized": anonymized,
        "total": len(matches),
        "items": [to_dict(m, anonymized=anonymized) for m in matches],
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def render_markdown(matches: list[FailedMatch], output_path: Path, anonymized: bool = False) -> None:
    lines = [
        "# Failed-Match Report",
        "",
    ]

    if anonymized:
        lines += [
            "> **Anonymisiert** — Dateinamen wurden durch Pseudonyme ersetzt.",
            "> Dieser Report kann zur Fehleranalyse weitergegeben werden.",
            "",
        ]

    lines += [
        f"Erstellt: {_now_iso()}  ",
        f"Einträge: {len(matches)}",
        "",
    ]

    if not matches:
        lines.append("Keine fehlgeschlagenen Treffer gefunden.")
    else:
        lines += [
            "| Datei | Score | Site-Hinweis | Datum-Hinweis |",
            "|---|---:|---|---|",
        ]
        for m in matches:
            score = f"{m.match_score:.2f}" if m.match_score is not None else "—"
            site = m.site_hint or "—"
            date = m.date_hint or "—"
            name = m.file_path.name
            lines.append(f"| {name} | {score} | {site} | {date} |")

        lines += [
            "",
            "## Details",
            "",
        ]
        for m in matches:
            lines += [
                f"### {m.file_path.name}",
                "",
                f"- **Pfad:** `{m.file_path}`",
                f"- **Log:** `{m.log_path}`",
                f"- **Score:** {m.match_score if m.match_score is not None else '—'}",
                f"- **Site-Hinweis:** {m.site_hint or '—'}",
                f"- **Datum-Hinweis:** {m.date_hint or '—'}",
            ]
            if not anonymized:
                lines.append(f"- **Datei vorhanden:** {'ja' if m.file_path.exists() else 'nein'}")
            lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def render_report(
    matches: list[FailedMatch],
    output_dir: Path,
    fmt: str = "both",
    anonymize: bool = False,
) -> list[Path]:
    """Write report(s) to output_dir.

    fmt: 'markdown', 'json', or 'both'
    anonymize: replace filenames with stable pseudonyms before writing
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if anonymize:
        matches = [_anonymize_match(m) for m in matches]
        base = f"failed_matches_anonymous_{timestamp}"
    else:
        base = f"failed_matches_{timestamp}"

    written: list[Path] = []

    if fmt in ("json", "both"):
        p = output_dir / f"{base}.json"
        render_json(matches, p, anonymized=anonymize)
        written.append(p)

    if fmt in ("markdown", "both"):
        p = output_dir / f"{base}.md"
        render_markdown(matches, p, anonymized=anonymize)
        written.append(p)

    return written
