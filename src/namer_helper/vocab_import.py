"""
Bulk pre-seed the self-learning vocabulary (Option 2).

Fetches studio/performer NAMES from one of three sources and feeds them into
vocabulary.learn(). This is a one-time head-start; the passive self-learning
(Option 1) keeps filling gaps afterwards.

Sources:
  stash    — the local StashApp GraphQL API (fast, no rate limits, covers the
             user's whole Stash library incl. StashDB-synced data)
  stashdb  — StashDB cloud (broad, rate-limited, needs API key)
  tpdb     — ThePornDB REST (broad, rate-limited, needs API key)

All fetchers are best-effort: errors are caught and returned, partial progress
is persisted page by page.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import requests

from namer_helper import vocabulary

_STASHDB_URL = "https://stashdb.org/graphql"
_TPDB_REST = "https://api.theporndb.net"

ProgressFn = Callable[[str], None]
StopFn = Callable[[], bool]


def _never() -> bool:
    return False


def _noop(_: str) -> None:
    pass


# ── local StashApp ────────────────────────────────────────────────────────────

_STASH_STUDIOS = "query($f:FindFilterType!){ findStudios(filter:$f){ count studios{ name } } }"
_STASH_PERFORMERS = "query($f:FindFilterType!){ findPerformers(filter:$f){ count performers{ name } } }"


def import_from_stash(
    config_dir: Path, *, url: str, api_key: str = "", timeout: int = 30,
    per_page: int = 200, max_pages: int = 100000, progress: ProgressFn = _noop,
    should_stop: StopFn = _never,
) -> dict:
    from namer_helper.stash_bridge.client import StashClient, StashError
    client = StashClient(url=url, api_key=api_key, timeout=timeout)
    if not client.is_available():
        return {"studios": 0, "performers": 0, "error": f"StashApp nicht erreichbar: {url}"}

    new_studios = new_perfs = 0
    try:
        for query, key, kind in (
            (_STASH_STUDIOS, "findStudios", "studios"),
            (_STASH_PERFORMERS, "findPerformers", "performers"),
        ):
            sub = "studios" if kind == "studios" else "performers"
            page = 1
            while page <= max_pages:
                if should_stop():
                    return {"studios": new_studios, "performers": new_perfs, "error": None, "stopped": True}
                data = client.query(query, {"f": {"per_page": per_page, "page": page}})
                block = (data or {}).get(key) or {}
                names = [n.get("name") for n in (block.get(sub) or []) if n.get("name")]
                if not names:
                    break
                added = vocabulary.learn(config_dir, **{kind: names})
                if kind == "studios":
                    new_studios += added
                else:
                    new_perfs += added
                progress(f"{kind}: Seite {page}, +{added} neu")
                page += 1
        return {"studios": new_studios, "performers": new_perfs, "error": None}
    except StashError as exc:
        return {"studios": new_studios, "performers": new_perfs, "error": str(exc)}
    except Exception as exc:
        return {"studios": new_studios, "performers": new_perfs, "error": str(exc)}


# ── StashDB cloud ─────────────────────────────────────────────────────────────

_STASHDB_STUDIOS = "query($i:StudioQueryInput!){ queryStudios(input:$i){ count studios{ name } } }"
_STASHDB_PERFORMERS = "query($i:PerformerQueryInput!){ queryPerformers(input:$i){ count performers{ name } } }"


def import_from_stashdb(
    config_dir: Path, *, api_key: str, per_page: int = 100,
    max_pages: int = 100000, progress: ProgressFn = _noop,
    should_stop: StopFn = _never,
) -> dict:
    if not api_key:
        return {"studios": 0, "performers": 0, "error": "StashDB API-Key fehlt"}
    headers = {"Content-Type": "application/json", "ApiKey": api_key}
    new_studios = new_perfs = 0
    try:
        for query, key, kind in (
            (_STASHDB_STUDIOS, "queryStudios", "studios"),
            (_STASHDB_PERFORMERS, "queryPerformers", "performers"),
        ):
            sub = "studios" if kind == "studios" else "performers"
            page = 1
            while page <= max_pages:
                if should_stop():
                    return {"studios": new_studios, "performers": new_perfs, "error": None, "stopped": True}
                r = requests.post(_STASHDB_URL, json={"query": query,
                    "variables": {"i": {"page": page, "per_page": per_page}}},
                    headers=headers, timeout=30)
                r.raise_for_status()
                body = r.json()
                if "errors" in body:
                    return {"studios": new_studios, "performers": new_perfs,
                            "error": "; ".join(e.get("message", "") for e in body["errors"])}
                block = (body.get("data") or {}).get(key) or {}
                names = [n.get("name") for n in (block.get(sub) or []) if n.get("name")]
                if not names:
                    break
                added = vocabulary.learn(config_dir, **{kind: names})
                if kind == "studios":
                    new_studios += added
                else:
                    new_perfs += added
                progress(f"{kind}: Seite {page}, +{added} neu")
                page += 1
        return {"studios": new_studios, "performers": new_perfs, "error": None}
    except requests.RequestException as exc:
        return {"studios": new_studios, "performers": new_perfs, "error": str(exc)}
    except Exception as exc:
        return {"studios": new_studios, "performers": new_perfs, "error": str(exc)}


# ── ThePornDB REST ────────────────────────────────────────────────────────────

def import_from_tpdb(
    config_dir: Path, *, api_key: str, max_pages: int = 100000,
    progress: ProgressFn = _noop, should_stop: StopFn = _never,
) -> dict:
    if not api_key:
        return {"studios": 0, "performers": 0, "error": "ThePornDB API-Key fehlt"}
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    new_studios = new_perfs = 0
    try:
        for path, kind in (("/sites", "studios"), ("/performers", "performers")):
            page = 1
            while page <= max_pages:
                if should_stop():
                    return {"studios": new_studios, "performers": new_perfs, "error": None, "stopped": True}
                r = requests.get(f"{_TPDB_REST}{path}", params={"page": page},
                                 headers=headers, timeout=30)
                r.raise_for_status()
                body = r.json()
                rows = body.get("data") or []
                names = [(row.get("name") or row.get("title")) for row in rows
                         if (row.get("name") or row.get("title"))]
                if not names:
                    break
                added = vocabulary.learn(config_dir, **{kind: names})
                if kind == "studios":
                    new_studios += added
                else:
                    new_perfs += added
                progress(f"{kind}: Seite {page}, +{added} neu")
                meta = body.get("meta") or {}
                last = meta.get("last_page")
                if last and page >= int(last):
                    break
                page += 1
        return {"studios": new_studios, "performers": new_perfs, "error": None}
    except requests.RequestException as exc:
        return {"studios": new_studios, "performers": new_perfs, "error": str(exc)}
    except Exception as exc:
        return {"studios": new_studios, "performers": new_perfs, "error": str(exc)}
