"""
Filename analysis via Ollama.

Cleans a raw filename, builds a structured prompt, parses the JSON response
into an OllamaResult. Ollama is always advisory — no automatic actions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from namer_helper.ollama_bridge.client import OllamaClient, OllamaError


_STRIP_RE = re.compile(
    r"""
    \.(?:mp4|mkv|avi|mov|flv|wmv|m4v)$   # extension
    | \b(?:720p|1080p|2160p|4k|480p)\b    # resolution
    | \b(?:x264|x265|hevc|h264|xvid)\b    # codec
    | \b(?:bluray|bdrip|webrip|webdl|hdtv|dvdrip)\b  # source
    | \b(?:aac|mp3|ac3|dts)\b             # audio
    | [-_.]+                              # separators → spaces
    """,
    re.IGNORECASE | re.VERBOSE,
)

_PROMPT_TEMPLATE = """\
You are a media file metadata assistant. Analyze the filename below and return ONLY a JSON object — no explanation, no markdown, just the JSON.

Filename: {filename}
Cleaned hint: {cleaned}

Return exactly this structure:
{{
  "cleaned_name": "<readable title, no technical tags>",
  "search_queries": ["<query 1>", "<query 2>", "<query 3>"],
  "confidence": <float 0.0-1.0>,
  "recommended_action": "<auto_rename|manual_review|skip>",
  "reason": "<one sentence>"
}}

Rules:
- confidence >= 0.85 → auto_rename only if title is unambiguous
- confidence 0.50-0.84 → manual_review
- confidence < 0.50 → skip
- search_queries should vary: full title, title+studio, title+year
"""


@dataclass
class OllamaResult:
    filename: str
    cleaned_name: str
    search_queries: list[str] = field(default_factory=list)
    confidence: float = 0.0
    recommended_action: str = "manual_review"
    reason: str = ""
    raw_response: str = field(default="", repr=False)
    error: str | None = None


def _clean_filename(filename: str) -> str:
    stem = Path(filename).stem
    cleaned = _STRIP_RE.sub(" ", stem)
    cleaned = re.sub(r" {2,}", " ", cleaned).strip()
    return cleaned


def _parse_response(raw: str, filename: str) -> OllamaResult:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return OllamaResult(
            filename=filename,
            cleaned_name=_clean_filename(filename),
            error=f"JSON parse error: {raw[:200]}",
        )

    return OllamaResult(
        filename=filename,
        cleaned_name=data.get("cleaned_name", _clean_filename(filename)),
        search_queries=data.get("search_queries", []),
        confidence=float(data.get("confidence", 0.0)),
        recommended_action=data.get("recommended_action", "manual_review"),
        reason=data.get("reason", ""),
        raw_response=raw,
    )


def analyze_filename(
    filename: str,
    client: OllamaClient,
    model: str = "llama3",
) -> OllamaResult:
    """Analyze a single filename via Ollama. Never raises — errors land in result.error."""
    cleaned = _clean_filename(filename)
    prompt = _PROMPT_TEMPLATE.format(filename=filename, cleaned=cleaned)

    try:
        raw = client.generate(model=model, prompt=prompt)
    except OllamaError as exc:
        return OllamaResult(
            filename=filename,
            cleaned_name=cleaned,
            error=str(exc),
        )

    return _parse_response(raw, filename)


def analyze_filenames(
    filenames: list[str],
    client: OllamaClient,
    model: str = "llama3",
) -> list[OllamaResult]:
    return [analyze_filename(f, client, model) for f in filenames]
