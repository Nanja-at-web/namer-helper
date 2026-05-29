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
