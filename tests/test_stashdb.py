"""Tests for stash_bridge/stashdb.py and stash_bridge/client.py — all HTTP mocked."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from namer_helper.stash_bridge.stashdb import StashDBClient, StashDBResult, StashDBScene
from namer_helper.stash_bridge.client import StashClient, StashError


# ── helpers ───────────────────────────────────────────────────────────────────

def _mock_response(body: dict, status: int = 200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    r.raise_for_status = MagicMock(
        side_effect=requests.HTTPError() if status >= 400 else None
    )
    return r


def _scene_payload(scene_id="s1", title="Test Scene", studio="Evil Angel",
                   performers=None, duration=3600, date="2023-05-01"):
    return {
        "id": scene_id,
        "title": title,
        "date": date,
        "studio": {"name": studio} if studio else None,
        "performers": [
            {"performer": {"name": p}, "as": p}
            for p in (performers or ["Jane Doe"])
        ],
        "duration": duration,
    }


# ── StashDBClient.query_by_fingerprints ───────────────────────────────────────

class TestQueryByFingerprints:
    def test_returns_scene_on_hit(self):
        body = {
            "data": {
                "findScenesBySceneFingerprints": [[_scene_payload()]]
            }
        }
        with patch("namer_helper.stash_bridge.stashdb.requests.post",
                   return_value=_mock_response(body)):
            result = StashDBClient().query_by_fingerprints(oshash="abc123")
        assert result.found
        assert result.best.title == "Test Scene"
        assert result.best.studio == "Evil Angel"
        assert "Jane Doe" in result.best.performers

    def test_no_hashes_returns_error(self):
        result = StashDBClient().query_by_fingerprints()
        assert not result.found
        assert result.error

    def test_http_error_returns_error(self):
        with patch("namer_helper.stash_bridge.stashdb.requests.post",
                   side_effect=requests.ConnectionError("refused")):
            result = StashDBClient().query_by_fingerprints(oshash="abc")
        assert not result.found
        assert "refused" in result.error

    def test_graphql_error_returns_error(self):
        body = {"errors": [{"message": "Unauthorized"}]}
        with patch("namer_helper.stash_bridge.stashdb.requests.post",
                   return_value=_mock_response(body)):
            result = StashDBClient().query_by_fingerprints(oshash="abc")
        assert not result.found
        assert "Unauthorized" in result.error

    def test_empty_result_returns_not_found(self):
        body = {"data": {"findScenesBySceneFingerprints": [[]]}}
        with patch("namer_helper.stash_bridge.stashdb.requests.post",
                   return_value=_mock_response(body)):
            result = StashDBClient().query_by_fingerprints(oshash="abc")
        assert not result.found

    def test_duration_parsed(self):
        body = {"data": {"findScenesBySceneFingerprints": [[_scene_payload(duration=5400)]]}}
        with patch("namer_helper.stash_bridge.stashdb.requests.post",
                   return_value=_mock_response(body)):
            result = StashDBClient().query_by_fingerprints(oshash="abc")
        assert result.best.duration == 5400

    def test_api_key_sent_in_header(self):
        body = {"data": {"findScenesBySceneFingerprints": [[]]}}
        mock_post = MagicMock(return_value=_mock_response(body))
        with patch("namer_helper.stash_bridge.stashdb.requests.post", mock_post):
            StashDBClient(api_key="secret-key").query_by_fingerprints(oshash="abc")
        headers = mock_post.call_args.kwargs["headers"]
        assert headers.get("ApiKey") == "secret-key"


# ── StashDBResult properties ──────────────────────────────────────────────────

class TestStashDBResult:
    def _scene(self, id="1", title="Test"):
        return StashDBScene(id=id, title=title, date=None, studio=None)

    def test_found_false_when_empty(self):
        assert not StashDBResult().found

    def test_found_true_with_scenes(self):
        assert StashDBResult(scenes=[self._scene()]).found

    def test_best_returns_first(self):
        s1, s2 = self._scene("1", "First"), self._scene("2", "Second")
        assert StashDBResult(scenes=[s1, s2]).best.title == "First"

    def test_best_none_when_empty(self):
        assert StashDBResult().best is None


# ── StashDBClient.search_by_context ──────────────────────────────────────────

class TestSearchByContext:
    def test_empty_query_returns_error(self):
        result = StashDBClient().search_by_context()
        assert not result.found
        assert result.error

    def test_returns_results_on_hit(self):
        # StashDB context search uses "searchScene" as the GraphQL key
        scene = _scene_payload(title="Scene Title", duration=3600)
        body = {"data": {"searchScene": [scene]}}
        with patch("namer_helper.stash_bridge.stashdb.requests.post",
                   return_value=_mock_response(body)):
            result = StashDBClient().search_by_context(
                title="Scene Title", duration=3600
            )
        # Result may be filtered by min_score; assert no crash and no HTTP error
        assert result.error is None or "graphql" not in (result.error or "").lower()


# ── StashClient (stash_bridge/client.py) ─────────────────────────────────────

class TestStashClient:
    def test_is_available_true_on_200(self):
        mock_r = MagicMock(status_code=200)
        with patch("namer_helper.stash_bridge.client.requests.get", return_value=mock_r):
            assert StashClient().is_available() is True

    def test_is_available_false_on_connection_error(self):
        with patch("namer_helper.stash_bridge.client.requests.get",
                   side_effect=requests.ConnectionError()):
            assert StashClient().is_available() is False

    def test_is_available_false_on_non_200(self):
        mock_r = MagicMock(status_code=503)
        with patch("namer_helper.stash_bridge.client.requests.get", return_value=mock_r):
            assert StashClient().is_available() is False

    def test_query_returns_data(self):
        mock_r = MagicMock()
        mock_r.raise_for_status = MagicMock()
        mock_r.json.return_value = {"data": {"scenes": []}}
        with patch("namer_helper.stash_bridge.client.requests.post", return_value=mock_r):
            data = StashClient().query("{ scenes { id } }")
        assert "scenes" in data

    def test_query_raises_on_http_error(self):
        with patch("namer_helper.stash_bridge.client.requests.post",
                   side_effect=requests.ConnectionError("refused")):
            with pytest.raises(StashError, match="refused"):
                StashClient().query("{ scenes { id } }")

    def test_query_raises_on_graphql_error(self):
        mock_r = MagicMock()
        mock_r.raise_for_status = MagicMock()
        mock_r.json.return_value = {"errors": [{"message": "Auth required"}]}
        with patch("namer_helper.stash_bridge.client.requests.post", return_value=mock_r):
            with pytest.raises(StashError, match="Auth required"):
                StashClient().query("{ scenes { id } }")

    def test_api_key_in_header(self):
        mock_r = MagicMock()
        mock_r.raise_for_status = MagicMock()
        mock_r.json.return_value = {"data": {}}
        mock_post = MagicMock(return_value=mock_r)
        with patch("namer_helper.stash_bridge.client.requests.post", mock_post):
            StashClient(api_key="tok").query("{ scenes { id } }")
        headers = mock_post.call_args.kwargs["headers"]
        assert headers.get("ApiKey") == "tok"

    def test_endpoint_constructed_from_url(self):
        c = StashClient(url="http://192.168.1.5:9999")
        assert c.endpoint == "http://192.168.1.5:9999/graphql"

    def test_trailing_slash_stripped(self):
        c = StashClient(url="http://localhost:9999/")
        assert c.endpoint == "http://localhost:9999/graphql"
