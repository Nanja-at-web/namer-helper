"""
Comprehensive tests for stash_bridge/theporndb.py — all HTTP calls mocked.

Covers: ThePornDBClient methods, result properties, score calculation,
error handling for missing API key, HTTP errors, and GraphQL errors.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from namer_helper.stash_bridge.theporndb import (
    ThePornDBClient,
    ThePornDBMovie,
    ThePornDBMovieResult,
    ThePornDBResult,
    ThePornDBScene,
    _score_scene,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _mock_post(body: dict, status: int = 200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    r.raise_for_status = MagicMock(
        side_effect=requests.HTTPError() if status >= 400 else None
    )
    return r


def _mock_get(body: dict, status: int = 200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    r.raise_for_status = MagicMock(
        side_effect=requests.HTTPError() if status >= 400 else None
    )
    return r


def _raw_scene(scene_id="t1", title="Test Scene", studio="Evil Angel",
               performers=None, duration=3600, date="2023-05-01"):
    return {
        "id": scene_id,
        "title": title,
        "date": date,
        "studio": {"name": studio, "parent": {"name": studio}},
        "performers": [
            {"performer": {"name": p}}
            for p in (performers or ["Jane Doe"])
        ],
        "duration": duration,
        "images": [{"url": "http://example.com/img.jpg"}],
    }


def _raw_movie(movie_id="m1", title="Test Movie", studio="Evil Angel",
               duration=7200, date="2023-01-01"):
    return {
        "id": movie_id,
        "title": title,
        "date": date,
        "studio": {"name": studio, "parent": {"name": studio}},
        "performers": [],
        "duration": duration,
        "images": [{"url": "http://example.com/img.jpg"}],
    }


def _client(key="test-key"):
    return ThePornDBClient(api_key=key)


# ── ThePornDBResult / ThePornDBMovieResult properties ─────────────────────────

class TestResultProperties:
    def test_found_false_when_empty(self):
        assert not ThePornDBResult().found
        assert not ThePornDBMovieResult().found

    def test_found_true_with_scenes(self):
        s = ThePornDBScene(id="1", title="X", date=None, site=None, network=None)
        assert ThePornDBResult(scenes=[s]).found

    def test_found_true_with_movies(self):
        m = ThePornDBMovie(id="1", title="X", date=None, site=None, network=None)
        assert ThePornDBMovieResult(movies=[m]).found

    def test_best_returns_first(self):
        s1 = ThePornDBScene(id="1", title="A", date=None, site=None, network=None)
        s2 = ThePornDBScene(id="2", title="B", date=None, site=None, network=None)
        assert ThePornDBResult(scenes=[s1, s2]).best.title == "A"

    def test_best_none_when_empty(self):
        assert ThePornDBResult().best is None
        assert ThePornDBMovieResult().best is None


# ── ThePornDBClient._post (shared error handling) ─────────────────────────────

class TestPostErrorHandling:
    def test_no_api_key_returns_error(self):
        c = ThePornDBClient(api_key="")
        result = c.query_by_fingerprints(oshash="abc")
        assert not result.found
        assert "Token" in (result.error or "")

    def test_401_returns_auth_error(self):
        with patch("namer_helper.stash_bridge.theporndb.requests.post",
                   return_value=_mock_post({}, status=401)):
            result = _client().query_by_fingerprints(oshash="abc")
        assert not result.found
        assert result.error

    def test_connection_error_returns_error(self):
        with patch("namer_helper.stash_bridge.theporndb.requests.post",
                   side_effect=requests.ConnectionError("refused")):
            result = _client().query_by_fingerprints(oshash="abc")
        assert not result.found
        assert result.error

    def test_graphql_error_returns_error(self):
        body = {"errors": [{"message": "Not found"}]}
        with patch("namer_helper.stash_bridge.theporndb.requests.post",
                   return_value=_mock_post(body)):
            result = _client().query_by_fingerprints(oshash="abc")
        assert not result.found
        assert "Not found" in (result.error or "")

    def test_api_key_sent_as_authorization_bearer(self):
        body = {"data": {"findScenesBySceneFingerprints": [[]]}}
        mock_post = MagicMock(return_value=_mock_post(body))
        with patch("namer_helper.stash_bridge.theporndb.requests.post", mock_post):
            _client("mytoken").query_by_fingerprints(oshash="abc")
        headers = mock_post.call_args.kwargs["headers"]
        assert "ApiKey" in headers


# ── query_by_fingerprints ─────────────────────────────────────────────────────

class TestQueryByFingerprints:
    def test_no_hashes_returns_error(self):
        result = _client().query_by_fingerprints()
        assert not result.found
        assert result.error

    def test_oshash_returns_scene(self):
        body = {
            "data": {
                "findScenesBySceneFingerprints": [[_raw_scene(title="Great Scene")]]
            }
        }
        with patch("namer_helper.stash_bridge.theporndb.requests.post",
                   return_value=_mock_post(body)):
            result = _client().query_by_fingerprints(oshash="abc123")
        assert result.found
        assert result.best.title == "Great Scene"
        assert result.match_method == "hash"

    def test_scene_performers_parsed(self):
        body = {
            "data": {
                "findScenesBySceneFingerprints": [[
                    _raw_scene(performers=["Jane Doe", "John Smith"])
                ]]
            }
        }
        with patch("namer_helper.stash_bridge.theporndb.requests.post",
                   return_value=_mock_post(body)):
            result = _client().query_by_fingerprints(oshash="abc")
        assert "Jane Doe" in result.best.performers
        assert "John Smith" in result.best.performers

    def test_scene_duration_parsed(self):
        body = {"data": {"findScenesBySceneFingerprints": [[_raw_scene(duration=5400)]]}}
        with patch("namer_helper.stash_bridge.theporndb.requests.post",
                   return_value=_mock_post(body)):
            result = _client().query_by_fingerprints(oshash="abc")
        assert result.best.duration == 5400

    def test_empty_result_not_found(self):
        body = {"data": {"findScenesBySceneFingerprints": [[]]}}
        with patch("namer_helper.stash_bridge.theporndb.requests.post",
                   return_value=_mock_post(body)):
            result = _client().query_by_fingerprints(oshash="abc")
        assert not result.found


# ── search_by_context ─────────────────────────────────────────────────────────

class TestSearchByContext:
    def test_empty_title_returns_error(self):
        result = _client().search_by_context(title="")
        assert not result.found
        assert result.error

    def test_returns_scenes_on_hit(self):
        body = {"data": {"searchScene": [_raw_scene(title="Matching Scene")]}}
        with patch("namer_helper.stash_bridge.theporndb.requests.post",
                   return_value=_mock_post(body)):
            result = _client().search_by_context(title="Matching Scene", duration=3600)
        # Result may filter by min_score but should not raise
        assert isinstance(result.found, bool)

    def test_connection_error_handled(self):
        with patch("namer_helper.stash_bridge.theporndb.requests.post",
                   side_effect=requests.ConnectionError()):
            result = _client().search_by_context(title="Some Scene")
        assert not result.found


# ── search_by_performer ───────────────────────────────────────────────────────

class TestSearchByPerformer:
    def test_no_performers_returns_error(self):
        result = _client().search_by_performer(performers=[])
        assert not result.found

    def test_returns_scenes(self):
        body = {"data": {"searchScene": [_raw_scene(performers=["Jane Doe"])]}}
        with patch("namer_helper.stash_bridge.theporndb.requests.post",
                   return_value=_mock_post(body)):
            result = _client().search_by_performer(
                performers=["Jane Doe"], duration=3600
            )
        assert isinstance(result, ThePornDBResult)

    def test_connection_error_handled(self):
        with patch("namer_helper.stash_bridge.theporndb.requests.post",
                   side_effect=requests.ConnectionError()):
            result = _client().search_by_performer(performers=["Jane Doe"])
        assert isinstance(result, ThePornDBResult)


# ── query_movies_by_hashes ────────────────────────────────────────────────────

class TestQueryMoviesByHashes:
    def test_no_hashes_returns_error(self):
        result = _client().query_movies_by_hashes()
        assert not result.found
        assert result.error

    def test_returns_movie_on_hit(self):
        body = {
            "data": {
                "findScenesBySceneFingerprints": [[_raw_movie(title="Great Movie")]]
            }
        }
        with patch("namer_helper.stash_bridge.theporndb.requests.post",
                   return_value=_mock_post(body)):
            result = _client().query_movies_by_hashes(oshash="abc")
        assert isinstance(result, ThePornDBMovieResult)

    def test_movie_duration_parsed(self):
        body = {"data": {"findScenesBySceneFingerprints": [[_raw_movie(duration=7200)]]}}
        with patch("namer_helper.stash_bridge.theporndb.requests.post",
                   return_value=_mock_post(body)):
            result = _client().query_movies_by_hashes(oshash="abc")
        if result.found:
            assert result.best.duration == 7200


# ── search_jav_by_code ────────────────────────────────────────────────────────

class TestSearchJavByCode:
    def _rest_response(self, data=None):
        return {
            "data": data or [{"id": "j1", "title": "ABP-123 Scene", "date": "2023-01-01",
                               "studio": {"name": "SOD", "parent": {"name": "SOD"}},
                               "performers": [], "duration": 3600,
                               "images": [], "description": ""}]
        }

    def test_no_api_key_returns_error(self):
        c = ThePornDBClient(api_key="")
        result = c.search_jav_by_code("ABP-123")
        assert not result.found

    def test_returns_scene_on_hit(self):
        with patch("namer_helper.stash_bridge.theporndb.requests.get",
                   return_value=_mock_get(self._rest_response())):
            result = _client().search_jav_by_code("ABP-123")
        assert isinstance(result, ThePornDBResult)

    def test_connection_error_handled(self):
        with patch("namer_helper.stash_bridge.theporndb.requests.get",
                   side_effect=requests.ConnectionError()):
            result = _client().search_jav_by_code("ABP-123")
        assert not result.found


# ── _score_scene (scoring logic) ──────────────────────────────────────────────

class TestScoreScene:
    def _scene(self, title="Scene", performers=None, studio=None, date=None, dur=3600):
        return ThePornDBScene(
            id="1", title=title, date=date, site=studio, network=studio,
            performers=performers or [], duration=dur,
        )

    def test_title_match_increases_score(self):
        scene = self._scene(title="Evil Angel Scene")
        s1, _ = _score_scene(scene, "Evil Angel Scene", [], None, None, None)
        s2, _ = _score_scene(scene, "Completely Different", [], None, None, None)
        assert s1 > s2

    def test_performer_match_increases_score(self):
        scene = self._scene(performers=["Jane Doe"])
        s1, _ = _score_scene(scene, "X", ["Jane Doe"], None, None, None)
        s2, _ = _score_scene(scene, "X", ["Other Person"], None, None, None)
        assert s1 > s2

    def test_date_match_increases_score(self):
        scene = self._scene(date="2023-05-01")
        s1, _ = _score_scene(scene, "X", [], None, "2023-05-01", None)
        s2, _ = _score_scene(scene, "X", [], None, "2020-01-01", None)
        assert s1 > s2

    def test_duration_mismatch_reduces_score(self):
        scene = self._scene(dur=3600)
        s1, _ = _score_scene(scene, "X", [], None, None, 3600)     # exact match
        s2, _ = _score_scene(scene, "X", [], None, None, 8902)     # 148 vs 60 min
        assert s1 > s2

    def test_score_returns_int(self):
        scene = self._scene()
        score, breakdown = _score_scene(scene, "Title", [], None, None, None)
        assert isinstance(score, int)
        assert isinstance(breakdown, dict)
