"""
Optional semantic scene search via Ollama embeddings + ChromaDB.

This module is deliberately best-effort: missing chromadb, missing Ollama, or a
missing embedding model must never break the pre-check pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests


DEFAULT_MODEL = "nomic-embed-text"
DEFAULT_COLLECTION = "tpdb_scenes"
_DEFAULT_CHROMA = object()


@dataclass
class EmbeddingHit:
    id: str
    title: str
    score: float
    document: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EmbeddingResult:
    hits: list[EmbeddingHit] = field(default_factory=list)
    error: str | None = None
    source: str = "embedding"

    @property
    def found(self) -> bool:
        return bool(self.hits)


class OllamaEmbedder:
    def __init__(self, base_url: str = "http://localhost:11434", model: str = DEFAULT_MODEL, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def is_available(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if r.status_code != 200:
                return False
            models = [m.get("name", "") for m in r.json().get("models", [])]
            return any(name == self.model or name.startswith(f"{self.model}:") for name in models)
        except Exception:
            return False

    def embed(self, text: str) -> tuple[list[float] | None, str | None]:
        text = (text or "").strip()
        if not text:
            return None, "Kein Embedding-Text verfügbar"
        if not self.is_available():
            return None, f"Ollama Embedding-Modell fehlt: {self.model}"

        # Current Ollama embedding endpoint. Official docs: /api/embed.
        body, err = self._post_embed("/api/embed", {"model": self.model, "input": text})
        if body is not None:
            embeddings = body.get("embeddings") or []
            if embeddings and isinstance(embeddings[0], list):
                return [float(v) for v in embeddings[0]], None
            embedding = body.get("embedding")
            if isinstance(embedding, list):
                return [float(v) for v in embedding], None

        # Legacy endpoint fallback for older Ollama installations.
        body, legacy_err = self._post_embed("/api/embeddings", {"model": self.model, "prompt": text})
        if body is not None and isinstance(body.get("embedding"), list):
            return [float(v) for v in body["embedding"]], None
        return None, legacy_err or err or "Ollama Embedding fehlgeschlagen"

    def _post_embed(self, path: str, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        try:
            r = requests.post(f"{self.base_url}{path}", json=payload, timeout=self.timeout)
            if r.status_code == 404:
                return None, "Ollama Embedding-Endpunkt nicht gefunden"
            r.raise_for_status()
            return r.json(), None
        except requests.RequestException as exc:
            return None, str(exc)


class ChromaSceneIndex:
    def __init__(
        self,
        persist_dir: Path,
        *,
        collection_name: str = DEFAULT_COLLECTION,
        chroma_module: object = _DEFAULT_CHROMA,
    ) -> None:
        self.persist_dir = persist_dir
        self.collection_name = collection_name
        self.error: str | None = None
        self._collection = None

        try:
            if chroma_module is _DEFAULT_CHROMA:
                import chromadb as chroma_module  # type: ignore[no-redef]
            if chroma_module is None:
                raise ImportError("chromadb fehlt")
            client = chroma_module.PersistentClient(path=str(persist_dir))  # type: ignore[attr-defined]
            self._collection = client.get_or_create_collection(collection_name)
        except Exception as exc:
            self.error = str(exc) or "chromadb fehlt"

    @property
    def available(self) -> bool:
        return self._collection is not None

    def upsert(self, docs: list[dict[str, Any]], embedder: OllamaEmbedder) -> EmbeddingResult:
        if not self.available:
            return EmbeddingResult(error=self.error or "ChromaDB nicht verfügbar")
        ids, documents, metadatas, embeddings = [], [], [], []
        for doc in docs:
            doc_id = str(doc.get("id") or "").strip()
            title = str(doc.get("title") or "").strip()
            description = str(doc.get("description") or "").strip()
            text = " ".join(p for p in [title, description, str(doc.get("site") or ""), str(doc.get("performers") or "")] if p)
            if not doc_id or not text.strip():
                continue
            vector, err = embedder.embed(text)
            if err or vector is None:
                return EmbeddingResult(error=err)
            ids.append(doc_id)
            documents.append(text)
            metadatas.append(_clean_metadata(doc))
            embeddings.append(vector)
        if not ids:
            return EmbeddingResult(error="Keine indexierbaren Szenen")
        self._collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)
        return EmbeddingResult()

    def search(self, query: str, embedder: OllamaEmbedder, limit: int = 3) -> EmbeddingResult:
        if not self.available:
            return EmbeddingResult(error=self.error or "ChromaDB nicht verfügbar")
        vector, err = embedder.embed(query)
        if err or vector is None:
            return EmbeddingResult(error=err)
        try:
            raw = self._collection.query(query_embeddings=[vector], n_results=limit)
        except Exception as exc:
            return EmbeddingResult(error=str(exc))
        return EmbeddingResult(hits=_parse_hits(raw))


def search_scene_index(
    query: str,
    *,
    ollama_url: str,
    persist_dir: Path,
    model: str = DEFAULT_MODEL,
    limit: int = 3,
) -> EmbeddingResult:
    embedder = OllamaEmbedder(base_url=ollama_url, model=model)
    index = ChromaSceneIndex(persist_dir)
    return index.search(query, embedder, limit=limit)


def _clean_metadata(doc: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in ("title", "date", "site", "network", "url", "image", "sku"):
        value = doc.get(key)
        if value not in (None, "", []):
            metadata[key] = str(value)
    performers = doc.get("performers")
    if isinstance(performers, list):
        metadata["performers"] = ", ".join(str(p) for p in performers if p)
    elif performers:
        metadata["performers"] = str(performers)
    duration = doc.get("duration")
    if duration:
        metadata["duration"] = int(duration)
    return metadata


def _parse_hits(raw: dict[str, Any]) -> list[EmbeddingHit]:
    ids = (raw.get("ids") or [[]])[0]
    docs = (raw.get("documents") or [[]])[0]
    metas = (raw.get("metadatas") or [[]])[0]
    distances = (raw.get("distances") or [[]])[0]
    hits: list[EmbeddingHit] = []
    for idx, hit_id in enumerate(ids):
        metadata = metas[idx] if idx < len(metas) and isinstance(metas[idx], dict) else {}
        distance = distances[idx] if idx < len(distances) else None
        score = max(0.0, min(1.0, 1.0 - float(distance))) if distance is not None else 0.0
        hits.append(EmbeddingHit(
            id=str(hit_id),
            title=str(metadata.get("title") or hit_id),
            score=score,
            document=str(docs[idx]) if idx < len(docs) else "",
            metadata=metadata,
        ))
    return hits
