from namer_helper.stash_bridge.theporndb import ThePornDBClient


def test_tpdb_context_search_tries_multiple_terms_and_dedupes(monkeypatch):
    client = ThePornDBClient(api_key="token")
    calls = []

    def fake_post(query, variables):
        calls.append(variables["term"])
        if variables["term"] == "Performer A Shared Title":
            return {
                "data": {
                    "searchScene": [{
                        "id": "scene-1",
                        "title": "Shared Title",
                        "date": "2024-01-02",
                        "duration": 1200,
                        "images": [],
                        "studio": {"name": "Example Studio", "parent": None},
                        "performers": [
                            {"performer": {"name": "Performer A"}},
                            {"performer": {"name": "Performer B"}},
                        ],
                    }]
                }
            }, None
        return {"data": {"searchScene": []}}, None

    monkeypatch.setattr(client, "_post", fake_post)

    result = client.search_by_context(
        "Shared Title",
        performers=["Performer A", "Performer B"],
        studio="Example Studio",
        date="2024-01-02",
        duration=1205,
    )

    assert "Shared Title" in calls
    assert "Performer A Shared Title" in calls
    assert result.found
    assert result.best.id == "scene-1"
    assert result.best.score >= 80



def test_tpdb_movie_context_search_uses_rest_and_scores(monkeypatch):
    client = ThePornDBClient(api_key="token")
    calls = []

    def fake_get(path, params=None):
        calls.append((path, params or {}))
        if path == "/movies" and (params or {}).get("q") == "Feature Movie":
            return {
                "data": [{
                    "id": "movie-1",
                    "title": "Feature Movie",
                    "type": "Movie",
                    "date": "2020-05-06",
                    "duration": 9100,
                    "poster": "https://example.invalid/poster.jpg",
                    "site": {"name": "Movie Studio"},
                    "performers": [{"name": "Performer A"}],
                }]
            }, None
        return {"data": []}, None

    monkeypatch.setattr(client, "_get_rest", fake_get)

    result = client.search_movies_by_context(
        "Feature Movie",
        performers=["Performer A"],
        studio="Movie Studio",
        date="2020-05-06",
        duration=9106,
    )

    assert calls[0][0] == "/movies"
    assert result.found
    assert result.best.id == "movie-1"
    assert result.best.score >= 80


def test_tpdb_jav_search_uses_rest_sku_and_parses_scene(monkeypatch):
    client = ThePornDBClient(api_key="token")
    calls = []

    def fake_get(path, params=None):
        calls.append((path, params or {}))
        if path == "/jav" and (params or {}).get("sku") == "ABP-123":
            return {
                "data": [{
                    "id": "jav-1",
                    "title": "Example JAV Scene",
                    "date": "2024-01-02",
                    "duration": 1200,
                    "sku": "ABP-123",
                    "image": "https://example.invalid/jav.jpg",
                    "site": {"name": "Example Studio", "network": {"name": "Example Network"}},
                    "performers": [{"name": "Performer A"}],
                }]
            }, None
        return {"data": []}, None

    monkeypatch.setattr(client, "_get_rest", fake_get)

    result = client.search_jav_by_code("abp-123")

    assert calls[0] == ("/jav", {"sku": "ABP-123", "q": None, "per_page": 5})
    assert result.found
    assert result.match_method == "jav"
    assert result.best.id == "jav-1"
    assert result.best.match_method == "jav"
    assert result.best.score == 100
    assert result.best.site == "Example Studio"
    assert result.best.network == "Example Network"
    assert result.best.performers == ["Performer A"]
