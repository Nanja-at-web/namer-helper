"""
Tests for Ollama analyzer — all Ollama calls are mocked.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from namer_helper.ollama_bridge.analyzer import (
    OllamaResult,
    analyze_filename,
    analyze_filenames,
)
from namer_helper.ollama_bridge.client import OllamaClient, OllamaError


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_client(raw_response: str) -> OllamaClient:
    client = MagicMock(spec=OllamaClient)
    client.generate.return_value = raw_response
    return client


def _valid_response(
    cleaned_name: str = "Test Title",
    confidence: float = 0.75,
    action: str = "manual_review",
) -> str:
    return json.dumps({
        "cleaned_name": cleaned_name,
        "search_queries": ["Test Title Studio", "Test Title 2024"],
        "confidence": confidence,
        "recommended_action": action,
        "reason": "Dateiname enthält unklare Bestandteile",
    })


def _captured_prompt(client: OllamaClient) -> str:
    """Return the prompt string passed to the last client.generate() call."""
    return client.generate.call_args.kwargs["prompt"]


# ── prompt content ────────────────────────────────────────────────────────────

class TestPromptContent:
    def test_prompt_contains_cleaned_leet_name(self):
        # Leet filename: "3v1l.4ng3l.1080p.mp4" → cleaned: "evil.angel.1080p"
        # The prompt must contain the resolved (non-leet) version.
        client = _make_client(_valid_response())
        analyze_filename("3v1l.4ng3l.1080p.mp4", client)
        prompt = _captured_prompt(client)
        assert "evil" in prompt.lower()
        assert "angel" in prompt.lower()

    def test_prompt_raw_leet_not_in_cleaned_section(self):
        # The "Cleaned filename:" line must NOT contain raw leet characters.
        client = _make_client(_valid_response())
        analyze_filename("3v1l.4ng3l.mp4", client)
        prompt = _captured_prompt(client)
        lines = prompt.splitlines()
        cleaned_line = next(l for l in lines if l.startswith("Cleaned filename:"))
        assert "3v1l" not in cleaned_line
        assert "4ng3l" not in cleaned_line

    def test_prompt_shows_original_filename(self):
        # The original (un-normalised) filename must appear in the prompt too.
        client = _make_client(_valid_response())
        analyze_filename("3v1l.4ng3l.mp4", client)
        prompt = _captured_prompt(client)
        assert "3v1l.4ng3l.mp4" in prompt

    def test_prompt_noise_removed_from_cleaned(self):
        # Hash noise char removed in cleaned version
        client = _make_client(_valid_response())
        analyze_filename("cu#t.scene.mp4", client)
        prompt = _captured_prompt(client)
        cleaned_line = next(
            l for l in prompt.splitlines() if l.startswith("Cleaned filename:")
        )
        assert "#" not in cleaned_line

    def test_prompt_includes_context_performers(self):
        client = _make_client(_valid_response())
        analyze_filename("file.mp4", client, performers=["Jane Doe", "John Smith"])
        prompt = _captured_prompt(client)
        assert "Jane Doe" in prompt
        assert "John Smith" in prompt

    def test_prompt_includes_context_studio(self):
        client = _make_client(_valid_response())
        analyze_filename("file.mp4", client, studio="Evil Angel")
        prompt = _captured_prompt(client)
        assert "Evil Angel" in prompt
        assert "Studio/site:" in prompt

    def test_logo_studio_marked_low_confidence(self):
        # moondream output must NOT appear as a verified "Studio/site:" signal —
        # it should be labelled as low-confidence so the LLM doesn't trust it blindly.
        client = _make_client(_valid_response())
        analyze_filename("file.mp4", client, logo_studio="Brazzers")
        prompt = _captured_prompt(client)
        assert "Brazzers" in prompt
        assert "low confidence" in prompt.lower()
        assert "Studio/site: Brazzers" not in prompt  # not the verified label

    def test_confirmed_studio_takes_priority_over_logo(self):
        # When both studio (confirmed) and logo_studio (unverified) are present,
        # only the confirmed studio appears — no low-confidence line.
        client = _make_client(_valid_response())
        analyze_filename("file.mp4", client, studio="Evil Angel", logo_studio="Brazzers")
        prompt = _captured_prompt(client)
        assert "Evil Angel" in prompt
        assert "Studio/site: Evil Angel" in prompt
        assert "Brazzers" not in prompt  # logo_studio suppressed by confirmed studio
        assert "low confidence" not in prompt.lower()


# ── analyze_filename results ──────────────────────────────────────────────────

class TestAnalyzeFilename:
    def test_returns_result(self):
        client = _make_client(_valid_response())
        result = analyze_filename("Some.File.1080p.mp4", client)
        assert isinstance(result, OllamaResult)
        assert result.cleaned_name == "Test Title"
        assert result.confidence == pytest.approx(0.75)
        assert result.recommended_action == "manual_review"
        assert len(result.search_queries) == 2
        assert result.error is None

    def test_high_confidence(self):
        client = _make_client(_valid_response(confidence=0.92, action="auto_rename"))
        result = analyze_filename("clear.title.mp4", client)
        assert result.confidence >= 0.85
        assert result.recommended_action == "auto_rename"

    def test_low_confidence(self):
        client = _make_client(_valid_response(confidence=0.3, action="skip"))
        result = analyze_filename("abc123.mp4", client)
        assert result.recommended_action == "skip"

    def test_ollama_error_captured(self):
        client = MagicMock(spec=OllamaClient)
        client.generate.side_effect = OllamaError("connection refused")
        result = analyze_filename("file.mp4", client)
        assert result.error is not None
        assert "connection refused" in result.error
        assert result.confidence == 0.0

    def test_bad_json_captured(self):
        client = _make_client("this is not json at all")
        result = analyze_filename("file.mp4", client)
        assert result.error is not None
        assert "JSON parse error" in result.error

    def test_bad_json_fallback_uses_normalized_name(self):
        # When JSON parse fails, cleaned_name falls back to normalize(filename).normalized
        client = _make_client("bad json")
        result = analyze_filename("3v1l.4ng3l.mp4", client)
        # Fallback should be the normalized stem, not the raw leet filename
        assert result.cleaned_name == "evil.angel"

    def test_original_filename_preserved_in_result(self):
        client = _make_client(_valid_response())
        result = analyze_filename("3v1l.4ng3l.mp4", client)
        assert result.filename == "3v1l.4ng3l.mp4"

    def test_no_strip_re_in_module(self):
        # _STRIP_RE and _clean_filename must be deleted — not just unused.
        import namer_helper.ollama_bridge.analyzer as mod
        assert not hasattr(mod, "_STRIP_RE"), "_STRIP_RE must be deleted, not just unused"
        assert not hasattr(mod, "_clean_filename"), "_clean_filename must be deleted"


# ── analyze_filenames ─────────────────────────────────────────────────────────

class TestAnalyzeFilenames:
    def test_multiple_files(self):
        client = _make_client(_valid_response())
        results = analyze_filenames(["a.mp4", "b.mp4", "c.mp4"], client)
        assert len(results) == 3
        assert client.generate.call_count == 3

    def test_empty_list(self):
        client = _make_client(_valid_response())
        assert analyze_filenames([], client) == []
