"""
Tests for Stash Bridge — all GraphQL calls are mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from namer_helper.stash_bridge.client import StashClient, StashError
from namer_helper.stash_bridge.matcher import (
    StashResult,
    _clean_query,
    search_scene,
    search_scenes,
)


# --- _clean_query ---

def test_clean_query_removes_resolution():
    result = _clean_query("Studio.Title.1080p.x264.mp4")
    assert "1080p" not in result
    assert "x264" not in result


def test_clean_query_replaces_separators():
    result = _clean_query("Studio-Title.Scene.Name.mp4")
    assert "-" not in result
    assert "." not in result


def test_clean_query_strips_extension():
    result = _clean_query("title.mkv")
    assert "mkv" not in result


# --- search_scene ---

def _scene_payload(title: str = "Test Scene", studio: str = "Studio") -> dict:
    return {
        "findScenes": {
            "scenes": [
                {
                    "id": "1",
                    "title": title,
                    "date": "2024-03-01",
                    "studio": {"name": studio},
                    "performers": [{"name": "Performer A"}],
                    "files": [{"basename": "test.mp4", "path": "/media/test.mp4"}],
                }
            ]
        }
    }


def _empty_payload() -> dict:
    return {"findScenes": {"scenes": []}}


def _make_client(side_effects: list) -> StashClient:
    client = MagicMock(spec=StashClient)
    client.query.side_effect = side_effects
    return client


def test_path_match_returns_high_confidence():
    client = _make_client([_scene_payload()])
    result = search_scene("test.mp4", client)
    assert result.found
    assert result.best.confidence == pytest.approx(0.90)
    assert result.best.matched_by == "path"
    assert result.best.title == "Test Scene"
    assert result.best.studio == "Studio"
    assert result.best.performers == ["Performer A"]
    assert result.error is None


def test_title_fallback_when_path_empty():
    client = _make_client([_empty_payload(), _scene_payload()])
    result = search_scene("some.unknown.1080p.mp4", client)
    assert result.found
    assert result.best.matched_by == "title_search"
    assert result.best.confidence == pytest.approx(0.65)


def test_no_match_returns_empty():
    client = _make_client([_empty_payload(), _empty_payload()])
    result = search_scene("completely_unknown.mp4", client)
    assert not result.found
    assert result.best is None
    assert result.error is None


def test_stash_error_on_path_query():
    client = _make_client([StashError("connection refused")])
    result = search_scene("file.mp4", client)
    assert result.error is not None
    assert "connection refused" in result.error
    assert not result.found


def test_stash_error_on_title_query():
    client = _make_client([_empty_payload(), StashError("timeout")])
    result = search_scene("file.mp4", client)
    assert result.error is not None
    assert not result.found


def test_scene_date_and_no_studio():
    payload = {
        "findScenes": {
            "scenes": [{
                "id": "2",
                "title": "Solo Scene",
                "date": "2023-06-15",
                "studio": None,
                "performers": [],
                "files": [],
            }]
        }
    }
    client = _make_client([payload])
    result = search_scene("solo.mp4", client)
    assert result.found
    assert result.best.studio is None
    assert result.best.date == "2023-06-15"


def test_search_scenes_multiple():
    # a.mp4: path match (1 call)
    # b.mp4: path empty + title empty (2 calls)
    # c.mp4: path match (1 call)
    client = _make_client([
        _scene_payload(),   # a.mp4 path hit
        _empty_payload(),   # b.mp4 path miss
        _empty_payload(),   # b.mp4 title miss
        _scene_payload(),   # c.mp4 path hit
    ])
    results = search_scenes(["a.mp4", "b.mp4", "c.mp4"], client)
    assert len(results) == 3
    assert results[0].found
    assert not results[1].found
    assert results[2].found


def test_search_scenes_empty():
    client = MagicMock(spec=StashClient)
    results = search_scenes([], client)
    assert results == []
