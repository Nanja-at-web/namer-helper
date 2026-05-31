import json

from namer_helper.web import lookup_cache


def test_lookup_cache_discards_timeout_only_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(lookup_cache, "CACHE_DIR", tmp_path)
    entry = {
        "cache_version": lookup_cache.CACHE_VERSION,
        "identification": {"status": "unknown", "source": "timeout", "reason": "Timeout"},
        "stashdb_scenes": [],
        "tpdb_scenes": [],
        "tpdb_movies": [],
        "stashdb_error": "Timeout",
        "tpdb_error": "Timeout",
        "tpdb_movie_error": "Timeout",
    }
    path = tmp_path / "abc.json"
    path.write_text(json.dumps(entry), encoding="utf-8")

    assert lookup_cache.get("abc") is None
    assert not path.exists()


def test_lookup_cache_keeps_partial_success_even_with_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(lookup_cache, "CACHE_DIR", tmp_path)
    entry = {
        "cache_version": lookup_cache.CACHE_VERSION,
        "identification": {"status": "identified", "action": "rename"},
        "stashdb_scenes": [],
        "tpdb_scenes": [{"title": "Scene", "image": ""}],
        "tpdb_movies": [],
        "tpdb_movie_error": "Timeout",
    }
    path = tmp_path / "abc.json"
    path.write_text(json.dumps(entry), encoding="utf-8")

    cached = lookup_cache.get("abc")

    assert cached is not None
    assert cached["cached"] is True
    assert path.exists()


def test_transient_failure_detection_for_uncached_result():
    assert lookup_cache.is_transient_failure({
        "identification": {"source": "timeout", "reason": "Timeout"},
        "stashdb_scenes": [],
        "tpdb_scenes": [],
        "tpdb_movies": [],
    })


def test_stable_no_match_is_cacheable():
    assert not lookup_cache.is_transient_failure({
        "identification": {"source": "none", "reason": "Keine eindeutige Identifikation"},
        "stashdb_scenes": [],
        "tpdb_scenes": [],
        "tpdb_movies": [],
        "stashdb_error": "Datei nicht gefunden oder zu klein für Hash",
        "tpdb_error": "Keine Treffer",
    })
