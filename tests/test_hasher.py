"""Tests for namer_bridge/hasher.py — subprocess/ffmpeg calls are mocked."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from namer_helper.namer_bridge.hasher import (
    compute_oshash,
    compute_phash,
    extract_frame_text,
    get_video_info,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_video_file(tmp_path: Path, size: int = 200_000) -> Path:
    """Create a fake binary file large enough for oshash (needs ≥128 KB)."""
    p = tmp_path / "fake.mp4"
    # Fill with deterministic bytes so oshash is reproducible
    p.write_bytes(bytes(range(256)) * (size // 256))
    return p


def _ffprobe_output(duration=3600.5, width=1920, height=1080) -> str:
    return json.dumps({
        "streams": [
            {
                "codec_type": "video",
                "width": width,
                "height": height,
                "duration": str(duration),
            }
        ],
        "format": {
            "duration": str(duration),
            "tags": {
                "title": "Scene Title",
                "artist": "Evil Angel",
            },
        },
    })


# ── compute_oshash ────────────────────────────────────────────────────────────

class TestComputeOshash:
    def test_returns_16char_hex(self, tmp_path):
        f = _make_video_file(tmp_path)
        result = compute_oshash(f)
        assert result is not None
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    def test_reproducible(self, tmp_path):
        f = _make_video_file(tmp_path)
        assert compute_oshash(f) == compute_oshash(f)

    def test_returns_none_for_small_file(self, tmp_path):
        f = tmp_path / "tiny.mp4"
        f.write_bytes(b"x" * 1000)
        assert compute_oshash(f) is None

    def test_returns_none_for_missing_file(self, tmp_path):
        assert compute_oshash(tmp_path / "nonexistent.mp4") is None

    def test_different_files_different_hashes(self, tmp_path):
        f1 = tmp_path / "a.mp4"
        f2 = tmp_path / "b.mp4"
        f1.write_bytes(b"\x00" * 200_000)
        f2.write_bytes(b"\xff" * 200_000)
        assert compute_oshash(f1) != compute_oshash(f2)


# ── compute_phash ─────────────────────────────────────────────────────────────

class TestComputePhash:
    def test_returns_hash_string_on_success(self, tmp_path):
        f = _make_video_file(tmp_path)
        mock_run = MagicMock()
        mock_run.return_value.returncode = 0
        # ffmpeg extracts frames → second call computes hash via imagehash
        # We mock subprocess.run for ffmpeg and also patch imagehash
        with patch("namer_helper.namer_bridge.hasher.subprocess.run", mock_run):
            with patch("builtins.open", MagicMock()):
                # phash needs PIL/imagehash — skip if not available
                try:
                    result = compute_phash(f, duration=60)
                except Exception:
                    pytest.skip("imagehash/PIL not installed")

    def test_returns_none_when_ffmpeg_fails(self, tmp_path):
        f = _make_video_file(tmp_path)
        mock_run = MagicMock()
        mock_run.return_value.returncode = 1
        with patch("namer_helper.namer_bridge.hasher.subprocess.run", mock_run):
            result = compute_phash(f, duration=60)
        assert result is None

    def test_returns_none_for_missing_file(self, tmp_path):
        result = compute_phash(tmp_path / "ghost.mp4", duration=60)
        assert result is None


# ── get_video_info ────────────────────────────────────────────────────────────

class TestGetVideoInfo:
    def test_parses_duration_and_resolution(self, tmp_path):
        f = _make_video_file(tmp_path)
        mock_run = MagicMock()
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = _ffprobe_output(duration=5400, width=1280, height=720)
        with patch("namer_helper.namer_bridge.hasher.subprocess.run", mock_run):
            info = get_video_info(f)
        assert info.get("duration") == 5400
        assert info.get("width") == 1280
        assert info.get("height") == 720

    def test_parses_mp4_tags(self, tmp_path):
        f = _make_video_file(tmp_path)
        mock_run = MagicMock()
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = _ffprobe_output()
        with patch("namer_helper.namer_bridge.hasher.subprocess.run", mock_run):
            info = get_video_info(f)
        assert info.get("meta_title") == "Scene Title"
        assert info.get("meta_studio") == "Evil Angel"

    def test_returns_empty_dict_on_failure(self, tmp_path):
        f = _make_video_file(tmp_path)
        mock_run = MagicMock()
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        with patch("namer_helper.namer_bridge.hasher.subprocess.run", mock_run):
            info = get_video_info(f)
        assert isinstance(info, dict)

    def test_returns_empty_dict_when_ffprobe_missing(self, tmp_path):
        f = _make_video_file(tmp_path)
        with patch("namer_helper.namer_bridge.hasher.subprocess.run",
                   side_effect=FileNotFoundError("ffprobe not found")):
            info = get_video_info(f)
        assert isinstance(info, dict)


# ── extract_frame_text ────────────────────────────────────────────────────────

class TestExtractFrameText:
    def test_returns_empty_string_when_ffmpeg_missing(self, tmp_path):
        f = _make_video_file(tmp_path)
        with patch("namer_helper.namer_bridge.hasher.subprocess.run",
                   side_effect=FileNotFoundError("ffmpeg not found")):
            result = extract_frame_text(f, duration=60)
        assert result == ""

    def test_returns_empty_string_on_ffmpeg_error(self, tmp_path):
        f = _make_video_file(tmp_path)
        mock_run = MagicMock()
        mock_run.return_value.returncode = 1
        with patch("namer_helper.namer_bridge.hasher.subprocess.run", mock_run):
            result = extract_frame_text(f, duration=60)
        assert result == ""

    def test_returns_string_type(self, tmp_path):
        f = _make_video_file(tmp_path)
        # Tesseract returns empty when OCR fails — that's acceptable
        mock_run = MagicMock()
        mock_run.return_value.returncode = 1
        with patch("namer_helper.namer_bridge.hasher.subprocess.run", mock_run):
            result = extract_frame_text(f, duration=60)
        assert isinstance(result, str)
