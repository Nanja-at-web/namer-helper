import types
from unittest.mock import patch

from namer_helper.embedding import ChromaSceneIndex, OllamaEmbedder, load_scene_documents


class _Response:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Collection:
    def __init__(self):
        self.upserts = []

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)

    def query(self, **kwargs):
        return {
            "ids": [["scene-1"]],
            "documents": [["A semantic document"]],
            "metadatas": [[{
                "title": "Semantic Scene",
                "site": "Example Site",
                "performers": "Performer A, Performer B",
                "duration": 1200,
                "url": "https://example.invalid/scene-1",
            }]],
            "distances": [[0.18]],
        }


class _Client:
    def __init__(self, path):
        self.path = path
        self.collection = _Collection()

    def get_or_create_collection(self, name):
        return self.collection


def _fake_chroma():
    module = types.SimpleNamespace()
    module.client = None
    clients = []

    def persistent_client(path):
        client = _Client(path)
        clients.append(client)
        return client

    module.PersistentClient = persistent_client
    module.clients = clients
    return module


def test_chroma_index_missing_dependency_is_graceful(tmp_path):
    index = ChromaSceneIndex(tmp_path, chroma_module=None)
    assert index.available is False
    result = index.search("query", OllamaEmbedder())
    assert not result.found
    assert result.error


def test_ollama_embedder_reports_missing_model():
    with patch("namer_helper.embedding.requests.get", return_value=_Response({"models": [{"name": "llama3"}]})):
        vector, err = OllamaEmbedder(model="nomic-embed-text").embed("hello")
    assert vector is None
    assert "nomic-embed-text" in err
    assert "all-minilm" in err


def test_ollama_embedder_uses_installed_fallback_model():
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json)
        return _Response({"embeddings": [[0.1, 0.2]]})

    with patch("namer_helper.embedding.requests.get", return_value=_Response({"models": [{"name": "all-minilm:latest"}]})):
        with patch("namer_helper.embedding.requests.post", side_effect=fake_post):
            vector, err = OllamaEmbedder(model="nomic-embed-text").embed("hello")

    assert err is None
    assert vector == [0.1, 0.2]
    assert calls[0]["model"] == "all-minilm:latest"


def test_embedding_index_upsert_and_search(tmp_path):
    chroma = _fake_chroma()
    index = ChromaSceneIndex(tmp_path, chroma_module=chroma)

    with patch("namer_helper.embedding.requests.get", return_value=_Response({"models": [{"name": "nomic-embed-text:latest"}]})):
        with patch("namer_helper.embedding.requests.post", return_value=_Response({"embeddings": [[0.1, 0.2, 0.3]]})):
            upserted = index.upsert([{
                "id": "scene-1",
                "title": "Semantic Scene",
                "description": "A searchable TPDB description",
                "site": "Example Site",
                "performers": ["Performer A"],
                "duration": 1200,
            }], OllamaEmbedder())
            result = index.search("searchable scene", OllamaEmbedder())

    assert upserted.error is None
    assert result.found
    assert result.hits[0].id == "scene-1"
    assert result.hits[0].title == "Semantic Scene"
    assert result.hits[0].score > 0.8


def test_load_scene_documents_from_tpdb_json(tmp_path):
    source = tmp_path / "tpdb.json"
    source.write_text("""
{
  "data": [{
    "uuid": "scene-1",
    "title": "Semantic Scene",
    "description": "Searchable description",
    "date": "2024-01-02",
    "duration": 1200,
    "sku": "ABC-123",
    "site": {"name": "Example Site", "network": {"name": "Example Network"}},
    "performers": [{"name": "Performer A"}, {"performer": {"name": "Performer B"}}],
    "posters": {"large": "https://example.invalid/poster.jpg"}
  }]
}
""", encoding="utf-8")

    docs = load_scene_documents(source)

    assert docs == [{
        "id": "scene-1",
        "title": "Semantic Scene",
        "description": "Searchable description",
        "date": "2024-01-02",
        "duration": 1200,
        "site": "Example Site",
        "network": "Example Network",
        "performers": ["Performer A", "Performer B"],
        "url": "",
        "image": "https://example.invalid/poster.jpg",
        "sku": "ABC-123",
    }]


def test_load_scene_documents_from_jsonl(tmp_path):
    source = tmp_path / "tpdb.jsonl"
    source.write_text(
        '{"id":"scene-1","title":"One"}\n'
        '{"id":"scene-2","title":"Two","performers":["Performer A"]}\n',
        encoding="utf-8",
    )

    docs = load_scene_documents(source)

    assert [d["id"] for d in docs] == ["scene-1", "scene-2"]
    assert docs[1]["performers"] == ["Performer A"]
