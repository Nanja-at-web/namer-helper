"""Tests for vocab_import.py — all network calls mocked."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from namer_helper import vocab_import, vocabulary


# ── local StashApp ────────────────────────────────────────────────────────────

class TestImportFromStash:
    def _fake_client(self, studios_pages, perf_pages):
        client = MagicMock()
        client.is_available.return_value = True
        calls = {"studios": list(studios_pages), "performers": list(perf_pages)}
        def query(q, variables):
            if "findStudios" in q:
                page = variables["f"]["page"]
                names = calls["studios"][page - 1] if page <= len(calls["studios"]) else []
                return {"findStudios": {"count": 1, "studios": [{"name": n} for n in names]}}
            else:
                page = variables["f"]["page"]
                names = calls["performers"][page - 1] if page <= len(calls["performers"]) else []
                return {"findPerformers": {"count": 1, "performers": [{"name": n} for n in names]}}
        client.query.side_effect = query
        return client

    def test_imports_studios_and_performers(self, tmp_path):
        client = self._fake_client(
            studios_pages=[["Evil Angel", "5K Porn"], []],   # page1, page2 empty → stop
            perf_pages=[["Jane Doe"], []],
        )
        with patch("namer_helper.stash_bridge.client.StashClient", return_value=client):
            res = vocab_import.import_from_stash(tmp_path, url="http://x:9999")
        assert res["error"] is None
        assert res["studios"] == 2
        assert res["performers"] == 1
        vocab = vocabulary.load(tmp_path)
        assert vocab.is_studio("Evil Angel")
        assert vocab.is_performer("Jane Doe")

    def test_unavailable_stash_returns_error(self, tmp_path):
        client = MagicMock()
        client.is_available.return_value = False
        with patch("namer_helper.stash_bridge.client.StashClient", return_value=client):
            res = vocab_import.import_from_stash(tmp_path, url="http://x:9999")
        assert "nicht erreichbar" in res["error"]


# ── StashDB cloud ─────────────────────────────────────────────────────────────

class TestImportFromStashDB:
    def _resp(self, body):
        r = MagicMock(); r.json.return_value = body; r.raise_for_status = MagicMock()
        return r

    def test_no_key(self, tmp_path):
        res = vocab_import.import_from_stashdb(tmp_path, api_key="")
        assert "Key" in res["error"]

    def test_imports(self, tmp_path):
        pages = {
            "queryStudios": [{"studios": [{"name": "Brazzers"}]}, {"studios": []}],
            "queryPerformers": [{"performers": [{"name": "Alexa Grace"}]}, {"performers": []}],
        }
        state = {"queryStudios": 0, "queryPerformers": 0}
        def post(url, json, headers, timeout):
            key = "queryStudios" if "queryStudios" in json["query"] else "queryPerformers"
            i = state[key]; state[key] += 1
            data = pages[key][i] if i < len(pages[key]) else {key.replace("query","").lower(): []}
            return self._resp({"data": {key: data}})
        with patch("namer_helper.vocab_import.requests.post", side_effect=post):
            res = vocab_import.import_from_stashdb(tmp_path, api_key="k")
        assert res["error"] is None
        assert res["studios"] == 1 and res["performers"] == 1

    def test_graphql_error(self, tmp_path):
        with patch("namer_helper.vocab_import.requests.post",
                   return_value=self._resp({"errors": [{"message": "Unauthorized"}]})):
            res = vocab_import.import_from_stashdb(tmp_path, api_key="k")
        assert "Unauthorized" in res["error"]


# ── ThePornDB REST ────────────────────────────────────────────────────────────

class TestImportFromTPDB:
    def _resp(self, body):
        r = MagicMock(); r.json.return_value = body; r.raise_for_status = MagicMock()
        return r

    def test_no_key(self, tmp_path):
        res = vocab_import.import_from_tpdb(tmp_path, api_key="")
        assert "Key" in res["error"]

    def test_imports_with_last_page(self, tmp_path):
        def get(url, params, headers, timeout):
            if "/sites" in url:
                return self._resp({"data": [{"name": "Vixen"}], "meta": {"last_page": 1}})
            return self._resp({"data": [{"name": "Riley Reid"}], "meta": {"last_page": 1}})
        with patch("namer_helper.vocab_import.requests.get", side_effect=get):
            res = vocab_import.import_from_tpdb(tmp_path, api_key="k")
        assert res["error"] is None
        assert res["studios"] == 1 and res["performers"] == 1
        vocab = vocabulary.load(tmp_path)
        assert vocab.is_studio("Vixen")
        assert vocab.is_performer("Riley Reid")

    def test_connection_error(self, tmp_path):
        with patch("namer_helper.vocab_import.requests.get",
                   side_effect=requests.ConnectionError("refused")):
            res = vocab_import.import_from_tpdb(tmp_path, api_key="k")
        assert "refused" in res["error"]
