"""
Optional semantic scene search via Ollama embeddings + ChromaDB.

This module is deliberately best-effort: missing chromadb, missing Ollama, or a
missing embedding model must never break the pre-check pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests


DEFAULT_MODEL = "nomic-embed-text"
FALLBACK_MODELS = ("all-minilm", "mxbai-embed-large")
DEFAULT_COLLECTION = "tpdb_scenes"
DEFAULT_MIN_SCORE = 0.25
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
        return self.resolve_model() is not None

    def resolve_model(self) -> str | None:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            r.raise_for_status()
            installed = [m.get("name", "") for m in r.json().get("models", [])]
        except Exception:
            return None

        for candidate in (self.model, *FALLBACK_MODELS):
            match = _matching_model(candidate, installed)
            if match:
                return match
        return None

    def embed(self, text: str) -> tuple[list[float] | None, str | None]:
        text = (text or "").strip()
        if not text:
            return None, "Kein Embedding-Text verfügbar"
        model = self.resolve_model()
        if not model:
            models = ", ".join((self.model, *FALLBACK_MODELS))
            return None, f"Ollama Embedding-Modell fehlt: {models}"

        # Current Ollama embedding endpoint. Official docs: /api/embed.
        body, err = self._post_embed("/api/embed", {"model": model, "input": text})
        if body is not None:
            embeddings = body.get("embeddings") or []
            if embeddings and isinstance(embeddings[0], list):
                return [float(v) for v in embeddings[0]], None
            embedding = body.get("embedding")
            if isinstance(embedding, list):
                return [float(v) for v in embedding], None

        # Legacy endpoint fallback for older Ollama installations.
        body, legacy_err = self._post_embed("/api/embeddings", {"model": model, "prompt": text})
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
            text = " ".join(p for p in [
                title,
                description,
                str(doc.get("site") or ""),
                str(doc.get("network") or ""),
                str(doc.get("performers") or ""),
                str(doc.get("date") or ""),
                str(doc.get("sku") or ""),
            ] if p)
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

    def search(
        self,
        query: str,
        embedder: OllamaEmbedder,
        limit: int = 3,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> EmbeddingResult:
        if not self.available:
            return EmbeddingResult(error=self.error or "ChromaDB nicht verfügbar")
        vector, err = embedder.embed(query)
        if err or vector is None:
            return EmbeddingResult(error=err)
        try:
            raw = self._collection.query(query_embeddings=[vector], n_results=limit)
        except Exception as exc:
            return EmbeddingResult(error=str(exc))
        hits = [hit for hit in _parse_hits(raw) if hit.score >= min_score]
        return EmbeddingResult(hits=hits)


def search_scene_index(
    query: str,
    *,
    ollama_url: str,
    persist_dir: Path,
    model: str = DEFAULT_MODEL,
    limit: int = 3,
    min_score: float = DEFAULT_MIN_SCORE,
) -> EmbeddingResult:
    embedder = OllamaEmbedder(base_url=ollama_url, model=model)
    index = ChromaSceneIndex(persist_dir)
    return index.search(query, embedder, limit=limit, min_score=min_score)


def load_scene_documents(path: Path) -> list[dict[str, Any]]:
    """Load TPDB scene/movie documents from JSON or JSONL exports."""
    raw_items = _load_raw_items(path)
    docs = [_normalize_scene_document(item) for item in raw_items if isinstance(item, dict)]
    return [doc for doc in docs if doc.get("id") and doc.get("title")]


def load_lookup_cache_documents(cache_dir: Path) -> list[dict[str, Any]]:
    """Extract TPDB scene/movie documents from pre-check lookup-cache files."""
    docs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(cache_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for key in ("tpdb_scenes", "tpdb_movies"):
            for item in data.get(key) or []:
                if not isinstance(item, dict):
                    continue
                doc = _normalize_scene_document(item)
                doc_id = str(doc.get("id") or "").strip()
                if not doc_id or not doc.get("title") or doc_id in seen:
                    continue
                seen.add(doc_id)
                docs.append(doc)
    return docs


def _load_raw_items(path: Path) -> list[Any]:
    if path.suffix.lower() == ".jsonl":
        items = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                items.append(json.loads(line))
        return items

    body = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("data", "scenes", "movies", "items", "results"):
            value = body.get(key)
            if isinstance(value, list):
                return value
        return [body]
    return []


def _normalize_scene_document(item: dict[str, Any]) -> dict[str, Any]:
    site = item.get("site") or item.get("studio") or {}
    network = item.get("network") or {}
    if isinstance(site, dict):
        network = network or site.get("network") or site.get("parent") or {}
        site_name = site.get("name")
    else:
        site_name = site

    performers = []
    for performer in item.get("performers") or []:
        if isinstance(performer, dict):
            name = performer.get("name") or (performer.get("performer") or {}).get("name")
        else:
            name = str(performer)
        if name:
            performers.append(str(name))

    images = item.get("images") or []
    image = item.get("image") or item.get("poster") or item.get("poster_image") or ""
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict):
            image = first.get("url") or image

    posters = item.get("posters")
    if isinstance(posters, dict):
        image = posters.get("large") or posters.get("full") or image

    scene_id = item.get("id") or item.get("uuid") or item.get("url")
    if not scene_id and (item.get("sku") or item.get("title")):
        scene_id = f"{item.get('sku') or 'tpdb'}:{item.get('title') or ''}"
    return {
        "id": str(scene_id or ""),
        "title": str(item.get("title") or item.get("name") or ""),
        "description": str(item.get("description") or item.get("details") or item.get("synopsis") or ""),
        "date": item.get("date") or item.get("release_date") or item.get("released"),
        "duration": item.get("duration"),
        "site": site_name,
        "network": network.get("name") if isinstance(network, dict) else network,
        "performers": performers,
        "url": item.get("url") or "",
        "image": image or "",
        "sku": item.get("sku") or item.get("code") or item.get("identifier"),
    }


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


def _matching_model(candidate: str, installed: list[str]) -> str | None:
    for name in installed:
        if name == candidate or name.startswith(f"{candidate}:"):
            return name
    return None


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
