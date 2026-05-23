"""
namer-helper web dashboard — FastAPI application factory.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from namer_helper.namer_bridge.config_reader import read_namer_paths
from namer_helper.namer_bridge.log_parser import collect_failed_matches
from namer_helper.reports.renderer import render_report
from namer_helper.web.mounts import (
    NAMER_DIRS,
    MountConfig,
    do_mount,
    do_unmount,
    is_mounted,
    load_mounts,
    save_mounts,
)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_SERVICE = "namer-watchdog"
_VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".flv"}
_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _service_status() -> str:
    result = subprocess.run(
        ["systemctl", "is-active", _SERVICE], capture_output=True, text=True
    )
    return result.stdout.strip()


def _dir_stats(namer_config: Path) -> dict[str, dict]:
    try:
        paths = read_namer_paths(namer_config)
    except Exception:
        paths = {}
    stats: dict[str, dict] = {}
    for name in ("watch", "work", "failed", "dest"):
        path: Path | None = paths.get(f"{name}_dir")  # type: ignore[assignment]
        if path and path.exists():
            files = [f for f in path.rglob("*") if f.is_file()]
            stats[name] = {"path": str(path), "count": len(files)}
        else:
            stats[name] = {"path": str(path) if path else "—", "count": 0}
    return stats


def _recent_reports(report_dir: Path, limit: int = 5) -> list[str]:
    if not report_dir.exists():
        return []
    reports = sorted(
        report_dir.glob("failed_matches_*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [p.name for p in reports[:limit]]


def _list_failed_files(failed_dir: Path) -> list[dict]:
    if not failed_dir.exists():
        return []
    items = []
    for f in sorted(failed_dir.rglob("*")):
        if f.is_file() and f.suffix.lower() in _VIDEO_EXTS:
            items.append({
                "name": f.name,
                "name_encoded": quote(f.name),
                "size_mb": round(f.stat().st_size / 1_048_576, 1),
                "has_log": (failed_dir / f"{f.name}.namer_failed.log").exists(),
            })
    return items


def _safe_path(directory: Path, name: str) -> Path | None:
    """Return resolved path only if it stays within directory (path traversal guard)."""
    resolved = (directory / name).resolve()
    try:
        resolved.relative_to(directory.resolve())
        return resolved
    except ValueError:
        return None


def create_app(
    namer_config: Path,
    report_output_dir: Path,
    helper_config_dir: Path = Path("/etc/namer-helper"),
) -> FastAPI:
    app = FastAPI(title="namer-helper dashboard")
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.filters["urlencode"] = lambda s: quote(str(s))

    # ── Dashboard ────────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        return templates.TemplateResponse(request, "dashboard.html", {
            "status": _service_status(),
            "stats": _dir_stats(namer_config),
            "reports": _recent_reports(report_output_dir),
        })

    # ── Service control ──────────────────────────────────────────────────────

    @app.post("/service/{action}")
    async def service_action(action: str):
        if action in ("start", "stop", "restart"):
            subprocess.run(["systemctl", action, _SERVICE], capture_output=True)
        return RedirectResponse("/", status_code=303)

    # ── Reports ──────────────────────────────────────────────────────────────

    def _do_generate(anonymize: bool) -> None:
        paths = read_namer_paths(namer_config)
        matches = collect_failed_matches(paths["failed_dir"])
        render_report(matches, report_output_dir, fmt="both", anonymize=anonymize)

    @app.post("/report/generate")
    async def generate_report():
        try:
            _do_generate(anonymize=False)
        except Exception:
            pass
        return RedirectResponse("/", status_code=303)

    @app.post("/report/generate/anonymous")
    async def generate_report_anonymous():
        try:
            _do_generate(anonymize=True)
        except Exception:
            pass
        return RedirectResponse("/", status_code=303)

    # ── Generic directory browser ────────────────────────────────────────────

    @app.get("/files/{dir_name}", response_class=HTMLResponse)
    async def list_files(request: Request, dir_name: str):
        try:
            paths = read_namer_paths(namer_config)
        except Exception:
            paths = {}
        target: Path | None = paths.get(f"{dir_name}_dir")  # type: ignore[assignment]
        files: list[dict] = []
        if target and target.exists():
            files = sorted(
                [
                    {
                        "name": f.name,
                        "size_mb": round(f.stat().st_size / 1_048_576, 1),
                        "relative": str(f.relative_to(target)),
                    }
                    for f in target.rglob("*")
                    if f.is_file()
                ],
                key=lambda x: x["name"],
            )
        return templates.TemplateResponse(request, "files.html", {
            "dir_name": dir_name,
            "files": files,
        })

    # ── Failed files with actions ────────────────────────────────────────────

    @app.get("/failed", response_class=HTMLResponse)
    async def failed_list(request: Request):
        try:
            paths = read_namer_paths(namer_config)
            failed_dir = paths["failed_dir"]
        except Exception:
            failed_dir = Path("/var/lib/namer/failed")
        return templates.TemplateResponse(request, "failed.html", {
            "files": _list_failed_files(failed_dir),
        })

    @app.post("/failed/retry")
    async def failed_retry(name: str):
        try:
            paths = read_namer_paths(namer_config)
            failed_dir = paths["failed_dir"]
            watch_dir = paths["watch_dir"]
            video = _safe_path(failed_dir, name)
            if video and video.exists():
                video.rename(watch_dir / name)
            for suffix in (".namer_failed.log", ".namer.json.gz"):
                assoc = _safe_path(failed_dir, f"{name}{suffix}")
                if assoc and assoc.exists():
                    assoc.unlink()
        except Exception:
            pass
        return RedirectResponse("/failed", status_code=303)

    @app.post("/failed/delete")
    async def failed_delete(name: str):
        try:
            paths = read_namer_paths(namer_config)
            failed_dir = paths["failed_dir"]
            for filename in (name, f"{name}.namer_failed.log", f"{name}.namer.json.gz"):
                target = _safe_path(failed_dir, filename)
                if target and target.exists():
                    target.unlink()
        except Exception:
            pass
        return RedirectResponse("/failed", status_code=303)

    @app.get("/failed/logfile", response_class=HTMLResponse)
    async def failed_logfile(request: Request, name: str):
        content = ""
        try:
            paths = read_namer_paths(namer_config)
            log = _safe_path(paths["failed_dir"], f"{name}.namer_failed.log")
            if log and log.exists():
                content = log.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
        return templates.TemplateResponse(request, "logfile.html", {
            "name": name,
            "content": content,
        })

    # ── Live log viewer ──────────────────────────────────────────────────────

    @app.get("/log", response_class=HTMLResponse)
    async def log_page(request: Request):
        return templates.TemplateResponse(request, "log.html", {})

    @app.get("/log/stream")
    async def log_stream():
        async def generate():
            proc = await asyncio.create_subprocess_exec(
                "journalctl", "-u", _SERVICE, "-f", "-n", "100",
                "--no-pager", "--output=short-iso",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                async for line in proc.stdout:  # type: ignore[union-attr]
                    clean = _ANSI_RE.sub("", line.decode().rstrip())
                    yield f"data: {clean}\n\n"
            finally:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Mount management ─────────────────────────────────────────────────────

    @app.get("/mounts", response_class=HTMLResponse)
    async def mounts_page(request: Request):
        mounts = load_mounts(helper_config_dir)
        status = [
            {"config": m, "mounted": is_mounted(m.target)}
            for m in mounts
        ]
        return templates.TemplateResponse(request, "mounts.html", {
            "mounts": status,
            "namer_dirs": NAMER_DIRS,
        })

    @app.post("/mounts/add")
    async def mounts_add(request: Request):
        try:
            body = await request.json()
            mounts = load_mounts(helper_config_dir)
            new_id = str(max((int(m.id) for m in mounts), default=0) + 1)
            mounts.append(MountConfig(
                id=new_id,
                protocol=body.get("protocol", "smb"),
                host=body.get("host", "").strip(),
                share=body.get("share", "").strip(),
                target=body.get("target", "").strip(),
                username=body.get("username", "").strip(),
                password=body.get("password", ""),
                label=body.get("label", "").strip(),
            ))
            save_mounts(helper_config_dir, mounts)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/mounts/mount")
    async def mounts_mount(mount_id: str):
        mounts = load_mounts(helper_config_dir)
        config = next((m for m in mounts if m.id == mount_id), None)
        if not config:
            return {"ok": False, "error": "Mount nicht gefunden"}
        ok, err = do_mount(config)
        return {"ok": ok, "error": err}

    @app.post("/mounts/unmount")
    async def mounts_unmount(mount_id: str):
        mounts = load_mounts(helper_config_dir)
        config = next((m for m in mounts if m.id == mount_id), None)
        if not config:
            return {"ok": False, "error": "Mount nicht gefunden"}
        ok, err = do_unmount(config.target)
        return {"ok": ok, "error": err}

    @app.post("/mounts/delete")
    async def mounts_delete(mount_id: str):
        mounts = load_mounts(helper_config_dir)
        mounts = [m for m in mounts if m.id != mount_id]
        save_mounts(helper_config_dir, mounts)
        return {"ok": True}

    return app
