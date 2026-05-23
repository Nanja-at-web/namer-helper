"""
Tests for Ollama analyzer — all Ollama calls are mocked.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from namer_helper.ollama_bridge.analyzer import (
    OllamaResult,
    _clean_filename,
    analyze_filename,
    analyze_filenames,
)
from namer_helper.ollama_bridge.client import OllamaClient, OllamaError


# --- _clean_filename ---

def test_clean_removes_extension():
    assert "some video title" in _clean_filename("some.video.title.mp4")


def test_clean_removes_resolution():
    result = _clean_filename("Studio.Title.1080p.x264.mp4")
    assert "1080p" not in result
    assert "x264" not in result


def test_clean_removes_source_tag():
    result = _clean_filename("Title.WEBDL.2024.mkv")
    assert "webdl" not in result.lower()


def test_clean_replaces_dots_with_spaces():
    result = _clean_filename("Some.Title.Here.mp4")
    assert "." not in result
    assert "Some" in result


# --- analyze_filename ---

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


def test_analyze_returns_result():
    client = _make_client(_valid_response())
    result = analyze_filename("Some.File.1080p.mp4", client)
    assert isinstance(result, OllamaResult)
    assert result.cleaned_name == "Test Title"
    assert result.confidence == pytest.approx(0.75)
    assert result.recommended_action == "manual_review"
    assert len(result.search_queries) == 2
    assert result.error is None


def test_analyze_high_confidence():
    client = _make_client(_valid_response(confidence=0.92, action="auto_rename"))
    result = analyze_filename("clear.title.mp4", client)
    assert result.confidence >= 0.85
    assert result.recommended_action == "auto_rename"


def test_analyze_low_confidence():
    client = _make_client(_valid_response(confidence=0.3, action="skip"))
    result = analyze_filename("abc123.mp4", client)
    assert result.recommended_action == "skip"


def test_analyze_ollama_error():
    client = MagicMock(spec=OllamaClient)
    client.generate.side_effect = OllamaError("connection refused")
    result = analyze_filename("file.mp4", client)
    assert result.error is not None
    assert "connection refused" in result.error
    assert result.confidence == 0.0


def test_analyze_bad_json():
    client = _make_client("this is not json at all")
    result = analyze_filename("file.mp4", client)
    assert result.error is not None
    assert "JSON parse error" in result.error


def test_analyze_filenames_multiple():
    client = _make_client(_valid_response())
    results = analyze_filenames(["a.mp4", "b.mp4", "c.mp4"], client)
    assert len(results) == 3
    assert client.generate.call_count == 3


def test_analyze_filenames_empty():
    client = _make_client(_valid_response())
    results = analyze_filenames([], client)
    assert results == []
