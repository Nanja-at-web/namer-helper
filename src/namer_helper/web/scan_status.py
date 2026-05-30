"""
Persistent status file for long-running pre-check scans.

The scan itself runs in the web process. This file keeps the visible state on
disk so a browser reload or close does not lose progress information.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

STATUS_DIR = Path("/opt/namer-helper/scan-status")
STATUS_FILE = STATUS_DIR / "pre-check.json"


def _now() -> int:
    return int(time.time())


def load() -> dict[str, Any]:
    try:
        if STATUS_FILE.exists():
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"active": False, "status": "idle", "total": 0, "done": 0, "items": []}


def save(state: dict[str, Any]) -> None:
    try:
        STATUS_DIR.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def start(names: list[str]) -> dict[str, Any]:
    scan_id = uuid.uuid4().hex
    state = {
        "active": True,
        "status": "running",
        "scan_id": scan_id,
        "started_at": _now(),
        "updated_at": _now(),
        "finished_at": None,
        "total": len(names),
        "done": 0,
        "current": None,
        "items": [
            {"name": name, "status": "pending", "ok": None, "error": None, "updated_at": None}
            for name in names
        ],
    }
    save(state)
    return state


def mark_running(scan_id: str, name: str) -> None:
    state = load()
    if state.get("scan_id") != scan_id:
        return
    state["active"] = True
    state["status"] = "running"
    state["current"] = name
    state["updated_at"] = _now()
    for item in state.get("items", []):
        if item.get("name") == name and item.get("status") == "pending":
            item["status"] = "running"
            item["updated_at"] = _now()
            break
    save(state)


def mark_done(
    scan_id: str,
    name: str,
    ok: bool,
    error: str | None = None,
    identification: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    state = load()
    if state.get("scan_id") != scan_id:
        return
    scan_status = state.get("status")
    done = 0
    for item in state.get("items", []):
        if item.get("name") == name:
            item["status"] = "done" if ok else "error"
            item["ok"] = ok
            item["error"] = error
            item["identification"] = identification or None
            item["result"] = result or None
            item["updated_at"] = _now()
        if item.get("status") in {"done", "error"}:
            done += 1
    state["done"] = done
    state["current"] = None
    state["status"] = scan_status
    state["updated_at"] = _now()
    save(state)


def finish(scan_id: str) -> None:
    state = load()
    if state.get("scan_id") != scan_id:
        return
    state["active"] = False
    state["status"] = "finished"
    state["current"] = None
    state["finished_at"] = _now()
    state["updated_at"] = _now()
    save(state)


def fail(scan_id: str, error: str) -> None:
    state = load()
    if state.get("scan_id") != scan_id:
        return
    state["active"] = False
    state["status"] = "error"
    state["error"] = error
    state["current"] = None
    state["finished_at"] = _now()
    state["updated_at"] = _now()
    save(state)


def pause(scan_id: str | None = None) -> dict[str, Any]:
    state = load()
    if scan_id and state.get("scan_id") != scan_id:
        return state
    if state.get("active"):
        state["status"] = "pause_requested"
        state["updated_at"] = _now()
        save(state)
    return state


def resume() -> dict[str, Any]:
    state = load()
    if state.get("status") in {"paused", "pause_requested"}:
        state["active"] = True
        state["status"] = "running"
        state["updated_at"] = _now()
        save(state)
    return state


def stop(scan_id: str | None = None) -> dict[str, Any]:
    state = load()
    if scan_id and state.get("scan_id") != scan_id:
        return state
    if state.get("active") or state.get("status") in {"paused", "pause_requested", "stop_requested"}:
        state["active"] = False
        state["status"] = "stopped"
        state["current"] = None
        state["finished_at"] = _now()
        state["updated_at"] = _now()
        save(state)
    return state


def set_paused(scan_id: str) -> None:
    state = load()
    if state.get("scan_id") != scan_id:
        return
    state["active"] = False
    state["status"] = "paused"
    state["current"] = None
    state["updated_at"] = _now()
    save(state)


def set_stopped(scan_id: str) -> None:
    state = load()
    if state.get("scan_id") != scan_id:
        return
    state["active"] = False
    state["status"] = "stopped"
    state["current"] = None
    state["finished_at"] = _now()
    state["updated_at"] = _now()
    save(state)


def pending_names(state: dict[str, Any]) -> list[str]:
    return [
        item.get("name", "")
        for item in state.get("items", [])
        if item.get("name") and item.get("status") in {"pending", "running"}
    ]
