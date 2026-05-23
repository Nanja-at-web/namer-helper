"""
Scene search and metadata extraction via StashApp GraphQL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from namer_helper.stash_bridge.client import StashClient, StashError


_FIND_SCENES_BY_TITLE = """
query FindScenesByTitle($query: String!) {
  findScenes(
    filter: { q: $query, per_page: 5 }
  ) {
    scenes {
      id
      title
      date
      studio { name }
      performers { name }
      files {
        basename
        path
      }
    }
  }
}
"""

_FIND_SCENES_BY_PATH = """
query FindScenesByPath($path: String!) {
  findScenes(
    scene_filter: {
      path: { value: $path, modifier: INCLUDES }
    }
    filter: { per_page: 5 }
  ) {
    scenes {
      id
      title
      date
      studio { name }
      performers { name }
      files {
        basename
        path
      }
    }
  }
}
"""

_STRIP_TECHNICAL = re.compile(
    r"\b(?:720p|1080p|2160p|4k|480p|x264|x265|hevc|h264|"
    r"bluray|bdrip|webrip|webdl|hdtv|dvdrip|aac|mp3|ac3)\b",
    re.IGNORECASE,
)


@dataclass
class StashScene:
    id: str
    title: str
    date: str | None
    studio: str | None
    performers: list[str] = field(default_factory=list)
    matched_by: str = ""
    confidence: float = 0.0


@dataclass
class StashResult:
    filename: str
    scenes: list[StashScene] = field(default_factory=list)
    error: str | None = None

    @property
    def best(self) -> StashScene | None:
        return self.scenes[0] if self.scenes else None

    @property
    def found(self) -> bool:
        return bool(self.scenes)


def _extract_scenes(data: dict, matched_by: str, confidence: float) -> list[StashScene]:
    raw = data.get("findScenes", {}).get("scenes", [])
    scenes = []
    for s in raw:
        scenes.append(StashScene(
            id=s.get("id", ""),
            title=s.get("title") or "",
            date=s.get("date"),
            studio=s.get("studio", {}).get("name") if s.get("studio") else None,
            performers=[p["name"] for p in s.get("performers", []) if p.get("name")],
            matched_by=matched_by,
            confidence=confidence,
        ))
    return scenes


def _clean_query(filename: str) -> str:
    stem = Path(filename).stem
    cleaned = _STRIP_TECHNICAL.sub(" ", stem)
    cleaned = re.sub(r"[-_.]", " ", cleaned)
    cleaned = re.sub(r" {2,}", " ", cleaned).strip()
    return cleaned


def search_scene(filename: str, client: StashClient) -> StashResult:
    """
    Search StashApp for a scene matching the given filename.
    Tries path match first (high confidence), then title search (medium).
    Never raises — errors land in StashResult.error.
    """
    result = StashResult(filename=filename)

    # 1. Path match — most reliable
    try:
        data = client.query(_FIND_SCENES_BY_PATH, {"path": filename})
        scenes = _extract_scenes(data, matched_by="path", confidence=0.90)
        if scenes:
            result.scenes = scenes
            return result
    except StashError as exc:
        result.error = str(exc)
        return result

    # 2. Title search from cleaned filename
    query = _clean_query(filename)
    if not query:
        return result

    try:
        data = client.query(_FIND_SCENES_BY_TITLE, {"query": query})
        scenes = _extract_scenes(data, matched_by="title_search", confidence=0.65)
        result.scenes = scenes
    except StashError as exc:
        result.error = str(exc)

    return result


def search_scenes(filenames: list[str], client: StashClient) -> list[StashResult]:
    return [search_scene(f, client) for f in filenames]
