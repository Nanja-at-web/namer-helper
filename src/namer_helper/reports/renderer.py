"""
Renders FailedMatch lists to Markdown and JSON reports.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from namer_helper.namer_bridge.log_parser import FailedMatch


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_dict(match: FailedMatch) -> dict:
    return {
        "file": str(match.file_path),
        "log": str(match.log_path),
        "match_score": match.match_score,
        "site_hint": match.site_hint,
        "date_hint": match.date_hint,
        "file_exists": match.file_path.exists(),
    }


def render_json(matches: list[FailedMatch], output_path: Path) -> None:
    payload = {
        "generated": _now_iso(),
        "total": len(matches),
        "items": [to_dict(m) for m in matches],
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def render_markdown(matches: list[FailedMatch], output_path: Path) -> None:
    lines = [
        f"# Failed-Match Report",
        f"",
        f"Erstellt: {_now_iso()}  ",
        f"Einträge: {len(matches)}",
        f"",
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
                f"",
                f"- **Pfad:** `{m.file_path}`",
                f"- **Log:** `{m.log_path}`",
                f"- **Score:** {m.match_score if m.match_score is not None else '—'}",
                f"- **Site-Hinweis:** {m.site_hint or '—'}",
                f"- **Datum-Hinweis:** {m.date_hint or '—'}",
                f"- **Datei vorhanden:** {'ja' if m.file_path.exists() else 'nein'}",
                f"",
            ]

    output_path.write_text("\n".join(lines), encoding="utf-8")


def render_report(
    matches: list[FailedMatch],
    output_dir: Path,
    fmt: str = "both",
) -> list[Path]:
    """Write report(s) to output_dir. fmt: 'markdown', 'json', or 'both'."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    written: list[Path] = []

    if fmt in ("json", "both"):
        p = output_dir / f"failed_matches_{timestamp}.json"
        render_json(matches, p)
        written.append(p)

    if fmt in ("markdown", "both"):
        p = output_dir / f"failed_matches_{timestamp}.md"
        render_markdown(matches, p)
        written.append(p)

    return written
