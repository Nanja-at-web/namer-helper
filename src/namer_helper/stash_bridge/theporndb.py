"""
ThePornDB scene lookup via Stash-Box GraphQL endpoint.

Two lookup strategies:
1. Fingerprint (oshash/phash) — definitive match, low coverage
2. Title + context search — uses performers/studio to score and rank results

Endpoint: https://theporndb.net/graphql (Stash-Box compatible)
Token: read automatically from namer.cfg porndb_token
"""

from __future__ import annotations

from dataclasses import dataclass, field

import requests


THEPORNDB_GRAPHQL_URL = "https://theporndb.net/graphql"
THEPORNDB_REST_URL = "https://api.theporndb.net"

_STOP_WORDS = {"a", "an", "the", "and", "in", "of", "to", "my", "her", "his", "on", "at", "is", "with"}

_FINGERPRINT_QUERY = """
query FindScenesByFingerprints($fingerprints: [[FingerprintQueryInput!]!]!) {
  findScenesBySceneFingerprints(fingerprints: $fingerprints) {
    id title date
    images { url }
    studio { name parent { name } }
    performers { performer { name } }
  }
}
"""

_SEARCH_QUERY = """
query SearchScene($term: String!) {
  searchScene(term: $term) {
    id title date duration
    images { url }
    studio { name parent { name } }
    performers { performer { name } }
  }
}
"""

_PERFORMER_SEARCH_QUERY = """
query SearchPerformer($term: String!) {
  searchPerformer(term: $term) {
    id
    name
    aliases
  }
}
"""


@dataclass
class ThePornDBScene:
    id: str
    title: str
    date: str | None
    site: str | None
    network: str | None
    performers: list[str] = field(default_factory=list)
    url: str = ""
    image: str = ""
    match_method: str = "hash"
    score: int = 0
    score_breakdown: dict = field(default_factory=dict)
    duration: int | None = None


@dataclass
class ThePornDBResult:
    scenes: list[ThePornDBScene] = field(default_factory=list)
    error: str | None = None
    match_method: str = "hash"

    @property
    def found(self) -> bool:
        return bool(self.scenes)

    @property
    def best(self) -> ThePornDBScene | None:
        return self.scenes[0] if self.scenes else None


@dataclass
class ThePornDBMovie:
    id: str
    title: str
    date: str | None
    site: str | None
    network: str | None
    performers: list[str] = field(default_factory=list)
    url: str = ""
    image: str = ""
    match_method: str = "hash"
    score: int = 0
    score_breakdown: dict = field(default_factory=dict)
    duration: int | None = None
    type: str = "Movie"


@dataclass
class ThePornDBMovieResult:
    movies: list[ThePornDBMovie] = field(default_factory=list)
    error: str | None = None
    match_method: str = "hash"

    @property
    def found(self) -> bool:
        return bool(self.movies)

    @property
    def best(self) -> ThePornDBMovie | None:
        return self.movies[0] if self.movies else None


def _score_scene(
    scene: ThePornDBScene,
    title: str,
    performers: list[str],
    studio: str | None,
    date: str | None,
    duration: int | None = None,
) -> tuple[int, dict]:
    """Score a TPDB scene against known context. Returns (total, breakdown)."""
    breakdown: dict[str, int] = {}

    # Title word overlap
    title_words = set(title.lower().split()) - _STOP_WORDS
    scene_words = set(scene.title.lower().split()) - _STOP_WORDS
    overlap = len(title_words & scene_words)
    if overlap:
        breakdown["Titel"] = overlap * 10

    # Performer match
    scene_perfs_lower = [p.lower() for p in scene.performers]
    perf_pts = 0
    matched_perfs = []
    for p in performers:
        p_lower = p.lower().strip()
        if not p_lower:
            continue
        for sp in scene_perfs_lower:
            if p_lower in sp or sp in p_lower:
                perf_pts += 25
                matched_perfs.append(p.split()[0])  # first name only for display
                break
    if perf_pts:
        breakdown[f"Performer ({', '.join(matched_perfs)})"] = perf_pts

    # Studio / site match
    studio_pts = 0
    if studio and scene.site:
        s1, s2 = studio.lower(), scene.site.lower()
        if s1 in s2 or s2 in s1:
            studio_pts += 15
    if studio and scene.network:
        s1, n = studio.lower(), scene.network.lower()
        if s1 in n or n in s1:
            studio_pts += 8
    if studio_pts:
        breakdown["Studio"] = studio_pts

    # Date match
    date_pts = 0
    if date and scene.date:
        if date[:4] == scene.date[:4]:
            date_pts += 10
        if date == scene.date:
            date_pts += 10
    if date_pts:
        breakdown["Datum"] = date_pts

    # Duration match / mismatch. A large mismatch is a strong negative signal:
    # a full movie must not be accepted as a short scene just because names overlap.
    dur_pts = 0
    dur_label = ""
    if duration and scene.duration:
        diff = abs(duration - scene.duration)
        tolerance = max(600, int(min(duration, scene.duration) * 0.35))
        if diff <= 10:
            dur_pts, dur_label = 40, f"Dauer ±{diff}s"
        elif diff <= 60:
            dur_pts, dur_label = 20, f"Dauer ±{diff}s"
        elif diff <= tolerance:
            dur_pts, dur_label = 5, f"Dauer ±{diff}s"
        else:
            dur_pts, dur_label = -60, "Dauer-Konflikt"
    if dur_pts:
        breakdown[dur_label] = dur_pts

    total = sum(breakdown.values())
    return total, breakdown


def _score_movie(
    movie: ThePornDBMovie,
    title: str,
    performers: list[str],
    studio: str | None,
    date: str | None,
    duration: int | None = None,
) -> tuple[int, dict]:
    # Same signals as scene scoring, but movies often lack exact duration.
    proxy = ThePornDBScene(
        id=movie.id, title=movie.title, date=movie.date, site=movie.site,
        network=movie.network, performers=movie.performers, duration=movie.duration,
    )
    return _score_scene(proxy, title, performers, studio, date, duration)


class ThePornDBClient:
    def __init__(self, api_key: str = "", timeout: int = 15) -> None:
        self._headers = {"Content-Type": "application/json"}
        self._rest_headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if api_key:
            self._headers["ApiKey"] = api_key
            self._rest_headers["Authorization"] = f"Bearer {api_key}"
        self._api_key = api_key
        self._timeout = timeout

    def _post(self, query: str, variables: dict) -> tuple[dict | None, str | None]:
        if not self._api_key:
            return None, "ThePornDB API-Token fehlt"
        try:
            r = requests.post(
                THEPORNDB_GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers=self._headers,
                timeout=self._timeout,
            )
            if r.status_code == 401:
                return None, "ThePornDB: API-Token ungültig"
            if r.status_code == 403:
                return None, "ThePornDB: Zugriff verweigert"
            r.raise_for_status()
        except requests.RequestException as exc:
            return None, str(exc)
        body = r.json()
        if "errors" in body:
            return None, "; ".join(e.get("message", str(e)) for e in body["errors"])
        return body, None

    def _parse_scenes(self, raw: list, method: str) -> list[ThePornDBScene]:
        scenes = []
        for s in raw:
            scene_id = s.get("id", "")
            studio_obj = s.get("studio") or {}
            parent_obj = studio_obj.get("parent") or {}
            performers = [
                p.get("performer", {}).get("name", "")
                for p in s.get("performers", [])
                if p.get("performer", {}).get("name")
            ]
            raw_dur = s.get("duration")
            duration = int(raw_dur) if raw_dur else None
            images = s.get("images") or []
            image = images[0].get("url", "") if images else ""
            scenes.append(ThePornDBScene(
                id=scene_id,
                title=s.get("title") or "",
                date=s.get("date"),
                site=studio_obj.get("name"),
                network=parent_obj.get("name"),
                performers=performers,
                url=f"https://theporndb.net/scenes/{scene_id}" if scene_id else "",
                image=image,
                match_method=method,
                duration=duration,
            ))
        return scenes

    def _parse_rest_scenes(self, raw: list, method: str) -> list[ThePornDBScene]:
        scenes: list[ThePornDBScene] = []
        for s in raw:
            scene_id = s.get("id") or s.get("uuid") or ""
            site_obj = s.get("site") or {}
            network_obj = site_obj.get("network") or site_obj.get("parent") or {}
            performers = [
                p.get("name", "") if isinstance(p, dict) else str(p)
                for p in (s.get("performers") or [])
                if (p.get("name") if isinstance(p, dict) else p)
            ]
            raw_dur = s.get("duration")
            image = s.get("image") or s.get("poster") or s.get("poster_image") or ""
            if isinstance(s.get("images"), list) and s["images"]:
                first_image = s["images"][0]
                if isinstance(first_image, dict):
                    image = first_image.get("url") or image
            if isinstance(s.get("posters"), dict):
                image = s["posters"].get("large") or s["posters"].get("full") or image
            scenes.append(ThePornDBScene(
                id=str(scene_id),
                title=s.get("title") or "",
                date=s.get("date"),
                site=site_obj.get("name") if isinstance(site_obj, dict) else None,
                network=network_obj.get("name") if isinstance(network_obj, dict) else None,
                performers=performers,
                url=s.get("url") or (f"https://api.theporndb.net/jav/{scene_id}" if scene_id else ""),
                image=image or "",
                match_method=method,
                score=100 if method == "jav" else 0,
                score_breakdown={"JAV-Code": 100} if method == "jav" else {},
                duration=int(raw_dur) if raw_dur else None,
            ))
        return scenes

    def query_by_fingerprints(
        self, *, oshash: str | None = None, phash: str | None = None
    ) -> ThePornDBResult:
        """Hash-based fingerprint lookup — definitive but low coverage."""
        fps = []
        if oshash:
            fps.append({"hash": oshash, "algorithm": "OSHASH"})
        if phash:
            fps.append({"hash": phash, "algorithm": "PHASH"})
        if not fps:
            return ThePornDBResult(error="Kein Hash verfügbar")
        body, err = self._post(_FINGERPRINT_QUERY, {"fingerprints": [fps]})
        if err:
            return ThePornDBResult(error=err)
        raw = (body.get("data") or {}).get("findScenesBySceneFingerprints") or []
        flat = [s for fs in raw for s in (fs or [])]
        return ThePornDBResult(scenes=self._parse_scenes(flat, "hash"), match_method="hash")

    def search_by_context(
        self,
        title: str,
        performers: list[str] | None = None,
        studio: str | None = None,
        date: str | None = None,
        duration: int | None = None,
        min_score: int = 20,
    ) -> ThePornDBResult:
        """Title search with performer/studio/date scoring and filtering.

        Builds an enriched search term from title + performers, then scores
        all candidates. Only scenes above min_score are returned, sorted best-first.
        """
        if not title.strip():
            return ThePornDBResult(error="Kein Suchtitel verfügbar")

        performers = performers or []

        terms: list[str] = []
        base_title = title.strip()
        clean_perfs = [p.strip() for p in performers[:3] if p.strip()]
        if base_title:
            terms.append(base_title)
        if clean_perfs and base_title:
            terms.append(f"{clean_perfs[0]} {base_title}")
        if len(clean_perfs) >= 2:
            terms.append(" ".join(clean_perfs[:2]))
            if date and len(date) >= 4:
                terms.append(f"{' '.join(clean_perfs[:2])} {date[:4]}")
        if studio and base_title:
            terms.append(f"{studio} {base_title}")
        if date and len(date) >= 4 and base_title:
            terms.append(f"{base_title} {date[:4]}")

        seen_terms: set[str] = set()
        seen_ids: set[str] = set()
        candidates: list[ThePornDBScene] = []
        last_error: str | None = None
        for term in terms:
            key = term.lower().strip()
            if not key or key in seen_terms:
                continue
            seen_terms.add(key)
            body, err = self._post(_SEARCH_QUERY, {"term": term})
            if err:
                last_error = err
                continue
            raw = ((body or {}).get("data") or {}).get("searchScene") or []
            for scene in self._parse_scenes(raw, "title"):
                if scene.id and scene.id not in seen_ids:
                    seen_ids.add(scene.id)
                    candidates.append(scene)

        if not candidates and last_error:
            return ThePornDBResult(error=last_error, match_method="title")

        # Score and filter
        for scene in candidates:
            scene.score, scene.score_breakdown = _score_scene(scene, title, performers, studio, date, duration)

        scored = [s for s in candidates if s.score >= min_score]
        scored.sort(key=lambda s: s.score, reverse=True)

        # If nothing passes threshold, return top-3 unfiltered so user can decide
        if not scored and candidates:
            candidates.sort(key=lambda s: s.score, reverse=True)
            return ThePornDBResult(scenes=candidates[:3], match_method="title")

        return ThePornDBResult(scenes=scored[:3], match_method="title")

    def search_by_performer(
        self,
        performers: list[str],
        studio: str | None = None,
        date: str | None = None,
        duration: int | None = None,
        min_score: int = 20,
    ) -> ThePornDBResult:
        """Search scenes using performer names as the primary signal.

        Builds targeted search terms combining each performer with date/studio,
        deduplicates candidates across all queries, scores and returns best matches.
        """
        if not performers:
            return ThePornDBResult(error="Keine Performer angegeben")

        # Build search terms — combined multi-performer first (most specific)
        terms: list[str] = []
        clean = [p.strip() for p in performers[:3] if p.strip()]
        if len(clean) >= 2:
            combined = " ".join(clean[:2])
            terms.append(combined)
            if date and len(date) >= 4:
                terms.append(f"{combined} {date[:4]}")
            if studio:
                terms.append(f"{combined} {studio}")
        for p in clean[:2]:
            terms.append(p)
            if date and len(date) >= 4:
                terms.append(f"{p} {date[:4]}")
            if studio:
                terms.append(f"{p} {studio}")

        seen_ids: set[str] = set()
        all_candidates: list[ThePornDBScene] = []

        for term in terms:
            body, err = self._post(_SEARCH_QUERY, {"term": term})
            if err or not body:
                continue
            raw = (body.get("data") or {}).get("searchScene") or []
            for scene in self._parse_scenes(raw, "performer"):
                if scene.id not in seen_ids:
                    seen_ids.add(scene.id)
                    all_candidates.append(scene)

        if not all_candidates:
            return ThePornDBResult(error="Keine Treffer via Performer-Suche")

        # Score all candidates with full context
        for scene in all_candidates:
            # Use first performer name as title hint (often in scene title)
            title_hint = performers[0] if performers else ""
            scene.score, scene.score_breakdown = _score_scene(
                scene, title_hint, performers, studio, date, duration
            )

        scored = [s for s in all_candidates if s.score >= min_score]
        scored.sort(key=lambda s: s.score, reverse=True)

        if not scored and all_candidates:
            all_candidates.sort(key=lambda s: s.score, reverse=True)
            return ThePornDBResult(scenes=all_candidates[:3], match_method="performer")

        return ThePornDBResult(scenes=scored[:3], match_method="performer")

    def search_jav_by_code(self, code: str) -> ThePornDBResult:
        """Direct JAV lookup via TPDB REST /jav.

        JAV filenames often encode the canonical scene identifier directly
        (for example ABP-123). Searching this field is more precise than the
        generic scene text search.
        """
        normalized = code.upper().strip()
        if not normalized:
            return ThePornDBResult(error="Kein JAV-Code verfügbar", match_method="jav")

        variants = [normalized]
        compact = normalized.replace("-", "")
        if compact != normalized:
            variants.append(compact)

        seen_ids: set[str] = set()
        scenes: list[ThePornDBScene] = []
        last_error: str | None = None
        for value in variants:
            body, err = self._get_rest("/jav", {"sku": value, "q": None, "per_page": 5})
            if err:
                last_error = err
                continue
            raw = (body or {}).get("data") or []
            for scene in self._parse_rest_scenes(raw, "jav"):
                if scene.id and scene.id not in seen_ids:
                    seen_ids.add(scene.id)
                    scenes.append(scene)
            if scenes:
                break

        if not scenes:
            body, err = self._get_rest("/jav", {"q": normalized, "per_page": 5})
            if err:
                last_error = err
            raw = (body or {}).get("data") if body else []
            for scene in self._parse_rest_scenes(raw or [], "jav"):
                if scene.id and scene.id not in seen_ids:
                    seen_ids.add(scene.id)
                    scenes.append(scene)

        if scenes:
            return ThePornDBResult(scenes=scenes[:3], match_method="jav")
        return ThePornDBResult(error=last_error or f"Kein JAV-Treffer für {normalized}", match_method="jav")

    def _get_rest(self, path: str, params: dict | None = None) -> tuple[dict | None, str | None]:
        if not self._api_key:
            return None, "ThePornDB API-Token fehlt"
        try:
            r = requests.get(
                f"{THEPORNDB_REST_URL}{path}",
                params={k: v for k, v in (params or {}).items() if v not in (None, "", [])},
                headers=self._rest_headers,
                timeout=self._timeout,
            )
            if r.status_code == 401:
                return None, "ThePornDB: API-Token ungültig"
            if r.status_code == 403:
                return None, "ThePornDB: Zugriff verweigert"
            if r.status_code == 404:
                return None, "ThePornDB: nicht gefunden"
            r.raise_for_status()
            return r.json(), None
        except requests.RequestException as exc:
            return None, str(exc)

    def _parse_movies(self, raw: list, method: str) -> list[ThePornDBMovie]:
        movies: list[ThePornDBMovie] = []
        for m in raw:
            movie_id = m.get("id") or m.get("uuid") or ""
            site_obj = m.get("site") or {}
            network_obj = site_obj.get("network") or site_obj.get("parent") or {}
            performers = [
                p.get("name", "") if isinstance(p, dict) else str(p)
                for p in (m.get("performers") or [])
                if (p.get("name") if isinstance(p, dict) else p)
            ]
            raw_dur = m.get("duration")
            image = m.get("poster") or m.get("image") or ""
            if isinstance(m.get("posters"), dict):
                image = m["posters"].get("large") or image
            movies.append(ThePornDBMovie(
                id=str(movie_id),
                title=m.get("title") or "",
                date=m.get("date"),
                site=site_obj.get("name") if isinstance(site_obj, dict) else None,
                network=network_obj.get("name") if isinstance(network_obj, dict) else None,
                performers=performers,
                url=f"https://api.theporndb.net/movies/{movie_id}" if movie_id else (m.get("url") or ""),
                image=image or "",
                match_method=method,
                duration=int(raw_dur) if raw_dur else None,
                type=m.get("type") or "Movie",
            ))
        return movies

    def query_movies_by_hashes(
        self, *, oshash: str | None = None, phash: str | None = None
    ) -> ThePornDBMovieResult:
        for hash_type, hash_value in (("OSHASH", oshash), ("PHASH", phash)):
            if not hash_value:
                continue
            body, err = self._get_rest(f"/movies/hash/{hash_value}", {"type": hash_type})
            if err and "nicht gefunden" not in err:
                return ThePornDBMovieResult(error=err, match_method="hash")
            if not body:
                continue
            raw = body.get("data") if isinstance(body, dict) else body
            if isinstance(raw, dict):
                return ThePornDBMovieResult(movies=self._parse_movies([raw], "hash"), match_method="hash")
            if isinstance(raw, list) and raw:
                return ThePornDBMovieResult(movies=self._parse_movies(raw, "hash"), match_method="hash")
        return ThePornDBMovieResult(error="Kein Movie-Fingerprint-Treffer", match_method="hash")

    def search_movies_by_context(
        self,
        title: str = "",
        performers: list[str] | None = None,
        studio: str | None = None,
        date: str | None = None,
        duration: int | None = None,
        min_score: int = 20,
    ) -> ThePornDBMovieResult:
        performers = performers or []
        terms: list[str] = []
        base_title = title.strip()
        clean_perfs = [p.strip() for p in performers[:3] if p.strip()]
        if base_title:
            terms.append(base_title)
        if clean_perfs and base_title:
            terms.append(f"{clean_perfs[0]} {base_title}")
        if len(clean_perfs) >= 2:
            terms.append(" ".join(clean_perfs[:2]))
        if studio and base_title:
            terms.append(f"{studio} {base_title}")

        seen_terms: set[str] = set()
        seen_ids: set[str] = set()
        candidates: list[ThePornDBMovie] = []
        last_error: str | None = None
        for term in terms:
            key = term.lower().strip()
            if not key or key in seen_terms:
                continue
            seen_terms.add(key)
            params = {"q": term, "per_page": 10}
            if date and len(date) >= 4:
                params["year"] = date[:4]
            if duration:
                params["duration"] = duration
            body, err = self._get_rest("/movies", params)
            if err:
                last_error = err
                continue
            raw = (body or {}).get("data") or []
            for movie in self._parse_movies(raw, "movie"):
                if movie.id and movie.id not in seen_ids:
                    seen_ids.add(movie.id)
                    candidates.append(movie)

        if not candidates:
            return ThePornDBMovieResult(error=last_error or "Keine Movie-Treffer", match_method="movie")

        for movie in candidates:
            movie.score, movie.score_breakdown = _score_movie(movie, title, performers, studio, date, duration)
        scored = [m for m in candidates if m.score >= min_score]
        scored.sort(key=lambda m: m.score, reverse=True)
        if not scored:
            candidates.sort(key=lambda m: m.score, reverse=True)
            return ThePornDBMovieResult(movies=candidates[:3], match_method="movie")
        return ThePornDBMovieResult(movies=scored[:3], match_method="movie")

    def query_by_hash(self, oshash: str) -> ThePornDBResult:
        """Compat alias."""
        return self.query_by_fingerprints(oshash=oshash)

    def search_by_title(self, title: str) -> ThePornDBResult:
        """Compat alias — no context scoring."""
        return self.search_by_context(title)
