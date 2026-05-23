"""
namer-helper web dashboard — FastAPI application factory.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from namer_helper.namer_bridge.config_reader import read_namer_paths
from namer_helper.namer_bridge.log_parser import collect_failed_matches
from namer_helper.reports.renderer import render_report

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_SERVICE = "namer-watchdog"


def _service_status() -> str:
    result = subprocess.run(
        ["systemctl", "is-active", _SERVICE],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()  # "active", "inactive", "failed"


def _dir_stats(namer_config: Path) -> dict[str, dict]:
    try:
        paths = read_namer_paths(namer_config)
    except Exception:
        paths = {}

    stats: dict[str, dict] = {}
    for name in ("watch", "work", "failed", "dest"):
        key = f"{name}_dir"
        path: Path | None = paths.get(key)  # type: ignore[assignment]
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


def create_app(namer_config: Path, report_output_dir: Path) -> FastAPI:
    app = FastAPI(title="namer-helper dashboard")
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "status": _service_status(),
            "stats": _dir_stats(namer_config),
            "reports": _recent_reports(report_output_dir),
        })

    @app.post("/service/{action}")
    async def service_action(action: str):
        if action in ("start", "stop", "restart"):
            subprocess.run(["systemctl", action, _SERVICE], capture_output=True)
        return RedirectResponse("/", status_code=303)

    @app.post("/report/generate")
    async def generate_report(request: Request):
        form = await request.form()
        anonymize = form.get("anonymize") == "1"
        try:
            paths = read_namer_paths(namer_config)
            matches = collect_failed_matches(paths["failed_dir"])
            render_report(matches, report_output_dir, fmt="both", anonymize=anonymize)
        except Exception:
            pass
        return RedirectResponse("/", status_code=303)

    @app.get("/files/{dir_name}", response_class=HTMLResponse)
    async def list_files(request: Request, dir_name: str):
        try:
            paths = read_namer_paths(namer_config)
        except Exception:
            paths = {}
        dir_map = {n: paths.get(f"{n}_dir") for n in ("watch", "work", "failed", "dest")}
        target: Path | None = dir_map.get(dir_name)  # type: ignore[assignment]
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
        return templates.TemplateResponse("files.html", {
            "request": request,
            "dir_name": dir_name,
            "files": files,
        })

    return app
