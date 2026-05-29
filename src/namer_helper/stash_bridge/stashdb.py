"""
StashDB community fingerprint database client.

Queries https://stashdb.org/graphql with oshash/phash fingerprints.
API key is required and must be stored in /etc/namer-helper/ai_config.json.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import requests


STASHDB_URL = "https://stashdb.org/graphql"

_QUERY = """
query FindScenesByFingerprints($fingerprints: [[FingerprintQueryInput!]!]!) {
  findScenesBySceneFingerprints(fingerprints: $fingerprints) {
    id title date duration
    studio { name }
    performers { performer { name } }
  }
}
"""

_PERFORMER_SEARCH_QUERY = """
query SearchPerformer($term: String!) {
  searchPerformer(term: $term) { id name }
}
"""

_PERFORMER_SCENES_QUERY = """
query FindPerformer($id: ID!) {
  findPerformer(id: $id) {
    scenes {
      id title date duration
      studio { name parent { name } }
      performers { performer { name } }
    }
  }
}
"""

_SEARCH_SCENE_QUERY = """
query SearchScene($term: String!) {
  searchScene(term: $term) {
    id title date duration
    studio { name }
    performers { performer { name } }
  }
}
"""


@dataclass
class StashDBScene:
    id: str
    title: str
    date: str | None
    studio: str | None
    performers: list[str] = field(default_factory=list)
    stashdb_url: str = ""
    duration: int | None = None


@dataclass
class StashDBResult:
    scenes: list[StashDBScene] = field(default_factory=list)
    error: str | None = None

    @property
    def found(self) -> bool:
        return bool(self.scenes)

    @property
    def best(self) -> StashDBScene | None:
        return self.scenes[0] if self.scenes else None


class StashDBClient:
    def __init__(self, api_key: str = "", timeout: int = 15) -> None:
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["ApiKey"] = api_key
        self._timeout = timeout

    def query_by_fingerprints(
        self,
        *,
        phash: str | None = None,
        oshash: str | None = None,
        duration: int | None = None,  # kept for compat, not sent to API
    ) -> StashDBResult:
        """Look up scenes by phash and/or oshash fingerprint."""
        fps = []
        if oshash:
            fps.append({"hash": oshash, "algorithm": "OSHASH"})
        if phash:
            fps.append({"hash": phash, "algorithm": "PHASH"})

        if not fps:
            return StashDBResult(error="Kein Hash verfügbar")

        try:
            r = requests.post(
                STASHDB_URL,
                json={"query": _QUERY, "variables": {"fingerprints": [fps]}},
                headers=self._headers,
                timeout=self._timeout,
            )
            r.raise_for_status()
        except requests.RequestException as exc:
            return StashDBResult(error=str(exc))

        body = r.json()
        if "errors" in body:
            msgs = [e.get("message", str(e)) for e in body["errors"]]
            return StashDBResult(error="; ".join(msgs))

        scenes = []
        data = body.get("data") or {}
        # API returns [[Scene]] — outer = per-file, inner = matching scenes
        raw = data.get("findScenesBySceneFingerprints") or []
        for s in (scene for file_scenes in raw for scene in (file_scenes or [])):
            scene_id = s.get("id", "")
            # performers: [{as: "...", performer: {name: "..."}}]
            performers = [
                p.get("performer", {}).get("name", "")
                for p in s.get("performers", [])
                if p.get("performer", {}).get("name")
            ]
            raw_dur = s.get("duration")
            scenes.append(StashDBScene(
                id=scene_id,
                title=s.get("title") or "",
                date=s.get("date"),
                studio=s.get("studio", {}).get("name") if s.get("studio") else None,
                performers=performers,
                stashdb_url=f"https://stashdb.org/scenes/{scene_id}" if scene_id else "",
                duration=int(raw_dur) if raw_dur else None,
            ))

        return StashDBResult(scenes=scenes)

    def search_by_context(
        self,
        title: str = "",
        performers: list[str] | None = None,
        studio: str | None = None,
        date: str | None = None,
        duration: int | None = None,
        min_score: int = 15,
    ) -> StashDBResult:
        """Search StashDB by title/performers/studio — fallback when fingerprint fails.

        Builds search terms from performers + title, deduplicates candidates,
        scores by duration/studio/date/performer overlap, returns best matches.
        """
        performers = performers or []
        if not title and not performers:
            return StashDBResult(error="Kein Suchtitel oder Performer angegeben")

        terms: list[str] = []
        # Combined multi-performer first (most specific)
        clean_perfs = [p.strip() for p in performers[:3] if p.strip()]
        if len(clean_perfs) >= 2:
            terms.append(" ".join(clean_perfs[:2]))
            if date:
                terms.append(f"{' '.join(clean_perfs[:2])} {date[:4]}")
        for p in clean_perfs[:2]:
            terms.append(p)
        if title:
            terms.append(title)
            if clean_perfs:
                terms.append(f"{clean_perfs[0]} {title}")

        seen_ids: set[str] = set()
        candidates: list[StashDBScene] = []

        for term in terms:
            if not term.strip():
                continue
            try:
                r = requests.post(
                    STASHDB_URL,
                    json={"query": _SEARCH_SCENE_QUERY, "variables": {"term": term}},
                    headers=self._headers,
                    timeout=self._timeout,
                )
                r.raise_for_status()
                body = r.json()
            except requests.RequestException:
                continue
            for s in (body.get("data") or {}).get("searchScene") or []:
                scene_id = s.get("id", "")
                if not scene_id or scene_id in seen_ids:
                    continue
                seen_ids.add(scene_id)
                scene_perfs = [
                    p.get("performer", {}).get("name", "")
                    for p in s.get("performers", [])
                    if p.get("performer", {}).get("name")
                ]
                candidates.append(StashDBScene(
                    id=scene_id,
                    title=s.get("title") or "",
                    date=s.get("date"),
                    studio=(s.get("studio") or {}).get("name"),
                    performers=scene_perfs,
                    stashdb_url=f"https://stashdb.org/scenes/{scene_id}",
                    duration=int(s["duration"]) if s.get("duration") else None,
                ))

        if not candidates:
            return StashDBResult(error="Keine StashDB-Treffer via Textsuche")

        perfs_lower = [p.lower() for p in performers]

        def _score(sc: StashDBScene) -> int:
            pts = 0
            if duration and sc.duration:
                diff = abs(duration - sc.duration)
                if diff <= 10:   pts += 40
                elif diff <= 60: pts += 20
                elif diff <= 300: pts += 5
            if studio and sc.studio:
                if studio.lower() in sc.studio.lower() or sc.studio.lower() in studio.lower():
                    pts += 15
            if date and sc.date:
                if date[:4] == sc.date[:4]: pts += 10
                if date == sc.date:         pts += 10
            scene_perfs_lower = [p.lower() for p in sc.performers]
            for p in perfs_lower:
                if any(p in sp or sp in p for sp in scene_perfs_lower):
                    pts += 25
            return pts

        candidates.sort(key=_score, reverse=True)
        scored = [s for s in candidates if _score(s) >= min_score]
        return StashDBResult(scenes=(scored or candidates)[:3])

    def search_by_performer(
        self,
        performers: list[str],
        studio: str | None = None,
        date: str | None = None,
        duration: int | None = None,
    ) -> StashDBResult:
        """Find scenes via StashDB performer database.

        Looks up each performer by name → gets their scene list →
        scores by studio/date/duration → returns best matches.
        """
        if not performers:
            return StashDBResult(error="Keine Performer angegeben")

        seen_ids: set[str] = set()
        all_scenes: list[StashDBScene] = []

        for perf_name in performers[:2]:
            perf_name = perf_name.strip()
            if not perf_name:
                continue
            # Step A: find performer ID
            try:
                r = requests.post(
                    STASHDB_URL,
                    json={"query": _PERFORMER_SEARCH_QUERY, "variables": {"term": perf_name}},
                    headers=self._headers, timeout=self._timeout,
                )
                r.raise_for_status()
                body = r.json()
            except requests.RequestException:
                continue
            perfs = (body.get("data") or {}).get("searchPerformer") or []
            if not perfs:
                continue
            perf_id = perfs[0]["id"]

            # Step B: get their scenes
            try:
                r2 = requests.post(
                    STASHDB_URL,
                    json={"query": _PERFORMER_SCENES_QUERY, "variables": {"id": perf_id}},
                    headers=self._headers, timeout=self._timeout,
                )
                r2.raise_for_status()
                body2 = r2.json()
            except requests.RequestException:
                continue
            raw_scenes = ((body2.get("data") or {}).get("findPerformer") or {}).get("scenes") or []

            for s in raw_scenes:
                scene_id = s.get("id", "")
                if scene_id in seen_ids:
                    continue
                seen_ids.add(scene_id)
                scene_perfs = [
                    p.get("performer", {}).get("name", "")
                    for p in s.get("performers", [])
                    if p.get("performer", {}).get("name")
                ]
                studio_obj = s.get("studio") or {}
                studio_name = studio_obj.get("name") or (studio_obj.get("parent") or {}).get("name")
                raw_dur = s.get("duration")
                all_scenes.append(StashDBScene(
                    id=scene_id,
                    title=s.get("title") or "",
                    date=s.get("date"),
                    studio=studio_name,
                    performers=scene_perfs,
                    stashdb_url=f"https://stashdb.org/scenes/{scene_id}" if scene_id else "",
                    duration=int(raw_dur) if raw_dur else None,
                ))

        if not all_scenes:
            return StashDBResult(error="Kein StashDB-Performer gefunden")

        # Score scenes: duration + studio + date + multi-performer overlap
        perfs_lower = [p.lower() for p in performers]

        def _score(s: StashDBScene) -> int:
            pts = 0
            if duration and s.duration:
                diff = abs(duration - s.duration)
                if diff <= 10:   pts += 40
                elif diff <= 60: pts += 20
                elif diff <= 300: pts += 5
            if studio and s.studio:
                if studio.lower() in s.studio.lower() or s.studio.lower() in studio.lower():
                    pts += 15
            if date and s.date:
                if date[:4] == s.date[:4]: pts += 10
                if date == s.date:         pts += 10
            # Extra points for each additional matching performer (multi-performer scenes)
            scene_perfs_lower = [p.lower() for p in s.performers]
            matched = sum(
                1 for p in perfs_lower
                if any(p in sp or sp in p for sp in scene_perfs_lower)
            )
            if matched >= 2:
                pts += (matched - 1) * 15  # bonus for each extra performer confirmed
            return pts

        all_scenes.sort(key=_score, reverse=True)
        # Return top-3 with score ≥ 5 (at least something matched) or all if nothing scored
        scored = [s for s in all_scenes if _score(s) >= 5]
        return StashDBResult(scenes=(scored or all_scenes)[:3])

    def submit_fingerprint(
        self,
        scene_id: str,
        *,
        oshash: str | None = None,
        phash: str | None = None,
        duration: int | None = None,
    ) -> dict:
        """Submit oshash and/or phash fingerprints for a StashDB scene.

        Contributes our file's fingerprints to the community database so future
        lookups with the same encode succeed automatically.
        Returns {"submitted": n, "errors": [...]}
        """
        _MUTATION = """
        mutation($input: FingerprintSubmission!) {
          submitFingerprint(input: $input)
        }
        """
        submitted = 0
        errors: list[str] = []

        for algo, hash_val in [("OSHASH", oshash), ("PHASH", phash)]:
            if not hash_val:
                continue
            try:
                r = requests.post(
                    STASHDB_URL,
                    json={
                        "query": _MUTATION,
                        "variables": {
                            "input": {
                                "scene_id": scene_id,
                                "fingerprint": {
                                    "hash": hash_val,
                                    "algorithm": algo,
                                    "duration": duration or 0,
                                },
                            }
                        },
                    },
                    headers=self._headers,
                    timeout=self._timeout,
                )
                r.raise_for_status()
                body = r.json()
                if body.get("errors"):
                    errors.append(f"{algo}: {body['errors'][0].get('message', '?')}")
                elif (body.get("data") or {}).get("submitFingerprint"):
                    submitted += 1
            except requests.RequestException as exc:
                errors.append(f"{algo}: {exc}")

        return {"submitted": submitted, "errors": errors}
