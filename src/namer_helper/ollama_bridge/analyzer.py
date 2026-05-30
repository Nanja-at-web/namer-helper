"""
Filename analysis via Ollama.

Normalises the raw filename (leet, noise, unicode) via normalize(), then
builds a structured prompt and parses the JSON response into an OllamaResult.
Ollama is always advisory — no automatic actions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from namer_helper.normalize import normalize
from namer_helper.ollama_bridge.client import OllamaClient, OllamaError


_PROMPT_TEMPLATE = """\
You are a media file metadata assistant. Identify the scene title and return ONLY a JSON object — no explanation, no markdown, just the JSON.

Original filename: {filename}
Cleaned filename:  {cleaned}
{context_block}
Return exactly this structure:
{{
  "cleaned_name": "<Studio - Date - Title (Performers)>",
  "search_queries": ["<query 1>", "<query 2>", "<query 3>"],
  "confidence": <float 0.0-1.0>,
  "recommended_action": "<auto_rename|manual_review|skip>",
  "reason": "<one sentence>"
}}

Rules:
- Use known performers/studio/date from context if provided — they are more reliable than the filename
- cleaned_name should follow: Studio - YYYY-MM-DD - Title (Performer1, Performer2)
- search_queries: use performer names + title variations to find the scene
- confidence >= 0.85 only if title is clearly identifiable
- confidence 0.50-0.84 → manual_review
- confidence < 0.50 → skip
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


def _parse_response(raw: str, filename: str, cleaned: str) -> OllamaResult:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return OllamaResult(
            filename=filename,
            cleaned_name=cleaned,
            error=f"JSON parse error: {raw[:200]}",
        )

    return OllamaResult(
        filename=filename,
        cleaned_name=data.get("cleaned_name", cleaned),
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
    performers: list[str] | None = None,
    studio: str | None = None,
    date: str | None = None,
    duration: int | None = None,
    logo_studio: str | None = None,
) -> OllamaResult:
    """Analyze a filename via Ollama with all available context signals.

    Normalises the filename (leet, noise, unicode) before sending to the LLM,
    so the model receives a clean input even for heavily obfuscated filenames.
    Never raises — errors land in result.error.
    """
    norm = normalize(filename)
    cleaned = norm.normalized

    # Build context block from all known signals
    ctx_lines: list[str] = []
    eff_studio = studio or logo_studio
    if performers:
        ctx_lines.append(f"Known performers: {', '.join(performers)}")
    if eff_studio:
        ctx_lines.append(f"Studio/site: {eff_studio}")
    if date:
        ctx_lines.append(f"Date: {date}")
    if duration:
        m, s = divmod(duration, 60)
        ctx_lines.append(f"Duration: {m}:{s:02d} ({duration}s)")

    context_block = ""
    if ctx_lines:
        context_block = "Known context (trust these over the filename):\n" + "\n".join(f"  {l}" for l in ctx_lines) + "\n\n"

    prompt = _PROMPT_TEMPLATE.format(
        filename=filename,
        cleaned=cleaned,
        context_block=context_block,
    )

    try:
        raw = client.generate(model=model, prompt=prompt)
    except OllamaError as exc:
        return OllamaResult(
            filename=filename,
            cleaned_name=cleaned,
            error=str(exc),
        )

    return _parse_response(raw, filename, cleaned)


def analyze_filenames(
    filenames: list[str],
    client: OllamaClient,
    model: str = "llama3",
) -> list[OllamaResult]:
    return [analyze_filename(f, client, model) for f in filenames]
