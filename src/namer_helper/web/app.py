"""
namer-helper web dashboard — FastAPI application factory.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from namer_helper.namer_bridge.config_reader import read_namer_paths, read_namer_porndb_token
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
from namer_helper.web.ai_config import AIConfig, load_ai_config, save_ai_config
from namer_helper.web.identification import build_identification
# proxmox imported lazily inside create_app — keeps it optional and testable
# without SSH infrastructure. Routes are registered only when import succeeds.

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_SERVICE = "namer-watchdog"
_VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".flv"}
_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_SCAN_ITEM_TIMEOUT_SECONDS = 600
# Page-size bounds for paginated lists. Rendering tens of thousands of rows
# crashes the browser, so per_page is clamped; the user picks within this range.
_DEFAULT_PER_PAGE = 500
_MIN_PER_PAGE = 50
_MAX_PER_PAGE = 2000
_PER_PAGE_OPTIONS = (100, 250, 500, 1000, 2000)


def _paginate(items: list, page: int, per_page: int) -> dict:
    """Slice items for a page. Returns the slice plus navigation metadata.

    per_page is clamped to [_MIN_PER_PAGE, _MAX_PER_PAGE] so a user can't crash
    the browser by requesting everything at once.
    """
    total = len(items)
    per_page = max(_MIN_PER_PAGE, min(int(per_page or _DEFAULT_PER_PAGE), _MAX_PER_PAGE))
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(int(page or 1), pages))
    start = (page - 1) * per_page
    return {
        "slice": items[start:start + per_page],
        "total": total,
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "start": start,
        "end": min(start + per_page, total),
        "per_page_options": _PER_PAGE_OPTIONS,
    }
_SINGLE_LOOKUP_TIMEOUT_SECONDS = 600   # keep browser lookup aligned with server-scan per-file timeout
_NON_TEXT_OLLAMA_MODELS = ("all-minilm", "mxbai-embed", "nomic-embed", "moondream")
_NAMER_TEMPLATE_RE = re.compile(r"\{([^{}]+)\}")


def _is_ignored_file(path: Path) -> bool:
    ignored_prefixes = (".", "._", "@__", ".@__")
    if any(part.startswith(ignored_prefixes) for part in path.parts):
        return True
    try:
        if path.stat().st_size <= 0:
            return True
    except OSError:
        return True
    return False


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
            files = [
                f for f in path.rglob("*")
                if f.is_file() and not _is_ignored_file(f)
            ]
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


def _list_sorted_out_files(dirs: list[Path]) -> list[dict]:
    """List video files in the sort-out folders, newest first. Sidecars hidden."""
    items: list[dict] = []
    seen: set[str] = set()
    for d in dirs:
        if not d.exists():
            continue
        for f in sorted(d.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in _VIDEO_EXTS:
                continue
            if _is_ignored_file(f):
                continue
            key = str(f.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                stat = f.stat()
                size_mb = round(stat.st_size / 1_048_576, 1)
                mtime = int(stat.st_mtime)
            except OSError:
                size_mb, mtime = 0, 0
            import datetime as _dt
            moved_at = (
                _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                if mtime else "—"
            )
            items.append({
                "name": f.name,
                "name_encoded": quote(f.name),
                "size_mb": size_mb,
                "mtime": mtime,
                "moved_at": moved_at,
            })
    items.sort(key=lambda i: i["mtime"], reverse=True)
    return items


def _list_failed_files(failed_dir: Path) -> list[dict]:
    if not failed_dir.exists():
        return []
    items = []
    for f in sorted(failed_dir.rglob("*")):
        if not f.is_file():
            continue
        if f.suffix.lower() not in _VIDEO_EXTS:
            continue
        if _is_ignored_file(f):
            continue
        try:
            size_mb = round(f.stat().st_size / 1_048_576, 1)
        except OSError:
            size_mb = 0
        try:
            stem = f.stem  # filename without extension, e.g. "movie" from "movie.mp4"
            has_json = (failed_dir / f"{stem}_namer.json.gz").exists()
        except OSError:
            has_json = False
        items.append({
            "name": f.name,
            "name_encoded": quote(f.name),
            "size_mb": size_mb,
            "has_log": has_json,  # kept as has_log for template compat
        })
    return items


def _read_cfg_value(cfg_path: Path, key: str) -> str:
    try:
        for line in cfg_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            current_key, sep, current_value = stripped.partition("=")
            if sep and current_key.strip() == key:
                return current_value.strip()
    except Exception:
        pass
    return ""


def _write_cfg_value(cfg_path: Path, key: str, value: str) -> bool:
    try:
        lines = cfg_path.read_text(encoding="utf-8").splitlines()
        new_lines, found = [], False
        for line in lines:
            current_key, sep, _ = line.strip().partition("=")
            if sep and current_key.strip() == key:
                new_lines.append(f"{key} = {value}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{key} = {value}")
        cfg_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def _compact_name(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _safe_preview_value(value: object) -> str:
    text = str(value or "").replace("/", " ").replace("\\", " ")
    text = re.sub(r'[<>:"|?*\x00-\x1f]', "", text)
    return re.sub(r"\s+", " ", text).strip()


def _safe_relative_preview(path_text: str) -> str | None:
    normalized = path_text.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return None
    normalized = re.sub(r"/+", "/", normalized).strip("/")
    parts = [p.strip() for p in normalized.split("/") if p.strip()]
    if not parts:
        return None
    if any(part in {".", ".."} for part in parts):
        return None
    return "/".join(parts)


def _render_namer_template(template: str, values: dict[str, object]) -> str | None:
    if not template.strip():
        return None

    def repl(match: re.Match) -> str:
        expr = match.group(1).strip()
        key, _, filter_expr = expr.partition(":")
        key = key.split("|", 1)[0].strip()
        value = values.get(key)
        if filter_expr.strip():
            try:
                from jinja2.sandbox import SandboxedEnvironment

                env = SandboxedEnvironment(autoescape=False)
                rendered_value = env.from_string("{{ value" + filter_expr.strip() + " }}").render(value=value)
                return _safe_preview_value(rendered_value)
            except Exception:
                pass
        return _safe_preview_value(value)

    rendered = _NAMER_TEMPLATE_RE.sub(repl, template)
    rendered = re.sub(r"\s+", " ", rendered)
    rendered = re.sub(r"\s*/\s*", "/", rendered)
    rendered = re.sub(r"\s+-\s+(?=(?:/|\.|$))", "", rendered)
    rendered = rendered.replace("[]", "")
    return _safe_relative_preview(rendered)


def _namer_template_for_type(cfg_path: Path, media_type: str) -> tuple[str, str]:
    specific = _read_cfg_value(cfg_path, f"new_relative_path_name_{media_type}")
    if specific:
        return specific, f"new_relative_path_name_{media_type}"
    generic = _read_cfg_value(cfg_path, "new_relative_path_name")
    return generic, "new_relative_path_name"


def _namer_path_preview(
    cfg_path: Path,
    *,
    original_name: str,
    hashes: dict | None = None,
    filename_parsed: dict | None = None,
    tpdb_scenes: list[dict] | None = None,
    tpdb_movies: list[dict] | None = None,
) -> dict | None:
    scene = (tpdb_scenes or [None])[0]
    movie = (tpdb_movies or [None])[0]
    parsed = filename_parsed or {}
    source = movie or scene
    if not source:
        return None

    match_method = str(source.get("match_method") or "")
    media_type = "movie" if movie else "jav" if match_method == "jav" or parsed.get("jav_code") else "scene"
    template, template_key = _namer_template_for_type(cfg_path, media_type)
    if not template:
        return None

    ext = Path(original_name).suffix.lstrip(".")
    site = source.get("site") or source.get("studio") or source.get("network") or parsed.get("studio") or ""
    network = source.get("network") or ""
    name = source.get("title") or parsed.get("cleaned") or Path(original_name).stem
    date = source.get("date") or parsed.get("date") or ""
    performers = source.get("performers") or parsed.get("performers") or []
    values = {
        "date": date,
        "year": str(date)[:4] if date else "",
        "description": "",
        "name": name,
        "scene": name,
        "site": _compact_name(site),
        "full_site": site,
        "parent": _compact_name(network),
        "full_parent": network,
        "network": _compact_name(network),
        "full_network": network,
        "performers": ", ".join(performers),
        "all_performers": ", ".join(performers),
        "performer-sites": "",
        "all_performer-sites": "",
        "act": "",
        "ext": ext,
        "source_file_name": Path(original_name).name,
        "source_file_stem": Path(original_name).stem,
        "video_codec": "",
        "audio_codec": "",
        "trans": "",
        "type": "movie" if media_type == "movie" else "scene",
        "resolution": (hashes or {}).get("resolution") or parsed.get("resolution") or "",
        "vr": "",
    }
    rendered = _render_namer_template(template, values)
    if not rendered:
        return None
    return {
        "path": rendered,
        "template": template,
        "template_key": template_key,
        "type": media_type,
    }


def _safe_path(directory: Path, name: str) -> Path | None:
    """Return resolved path only if it stays within directory (path traversal guard)."""
    resolved = (directory / name).resolve()
    try:
        resolved.relative_to(directory.resolve())
        return resolved
    except ValueError:
        return None


def _move_to_directory(src: Path, target_dir: Path) -> tuple[bool, str | None]:
    """Move src into target_dir without overwriting an existing file."""
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / src.name
    if dest.exists():
        return False, f"Zieldatei existiert bereits: {dest.name}"
    src.rename(dest)
    return True, None


def _clean_lxc_id(value: object) -> str:
    text = str(value or "103").strip()
    return text if re.fullmatch(r"\d+", text) else "103"


def _check_system_deps() -> None:
    """Log warnings for missing optional system binaries at startup."""
    from loguru import logger
    missing = []
    for binary in ("ffmpeg", "tesseract"):
        result = subprocess.run(["which", binary], capture_output=True)
        if result.returncode != 0:
            missing.append(binary)
    if missing:
        logger.warning(
            "Fehlende System-Binaries: {}. "
            "Pre-Check-Funktionen sind eingeschränkt. "
            "Debian/Ubuntu: apt install {}",
            ", ".join(missing),
            " ".join(missing),
        )


_moondream_available_cache: dict[str, bool] = {}


def _is_moondream_available(ollama_url: str) -> bool:
    """Check once per URL if moondream is pulled in Ollama."""
    if ollama_url in _moondream_available_cache:
        return _moondream_available_cache[ollama_url]
    try:
        import requests as _requests
        resp = _requests.get(
            f"{ollama_url.rstrip('/')}/api/tags", timeout=5
        )
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        available = any("moondream" in m for m in models)
    except Exception:
        available = False
    _moondream_available_cache[ollama_url] = available
    if not available:
        from loguru import logger
        logger.warning(
            "moondream nicht in Ollama gefunden ({}). "
            "Studio-Logo-Erkennung deaktiviert. "
            "Aktivieren mit: ollama pull moondream",
            ollama_url,
        )
    return available


def _list_ollama_models(ollama_url: str) -> tuple[list[str], str | None]:
    """Return installed Ollama model names for a settings dropdown."""
    if not ollama_url:
        return [], "Ollama URL fehlt"
    try:
        import requests as _requests
        resp = _requests.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=5)
        resp.raise_for_status()
        models = sorted(
            name for name in {
                str(m.get("name") or "").strip()
                for m in resp.json().get("models", [])
                if str(m.get("name") or "").strip()
            }
            if not any(hint in name.lower() for hint in _NON_TEXT_OLLAMA_MODELS)
        )
        return models, None
    except Exception as exc:
        return [], str(exc)


def create_app(
    namer_config: Path,
    report_output_dir: Path,
    helper_config_dir: Path = Path("/etc/namer-helper"),
) -> FastAPI:
    app = FastAPI(title="namer-helper dashboard")
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.filters["urlencode"] = lambda s: quote(str(s))

    # Lazy-import proxmox module — SSH extras are optional infrastructure.
    # Routes are only registered when the import succeeds.
    try:
        from namer_helper.web.proxmox import (
            ensure_ssh_key,
            load_proxmox_config,
            run_remote,
            save_proxmox_config,
            setup_host_mount,
            teardown_host_mount,
            ProxmoxConfig,
        )
        _proxmox_available = True
    except ImportError:
        _proxmox_available = False

    @app.on_event("startup")
    async def _startup_checks() -> None:
        from namer_helper.web import scan_status

        scan_status.stop_interrupted()
        _check_system_deps()
        ai_cfg = load_ai_config(helper_config_dir)
        if ai_cfg.ollama_url:
            _is_moondream_available(ai_cfg.ollama_url)

    # ── Dashboard ────────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        ai_cfg = load_ai_config(helper_config_dir)
        pre_dir = Path(ai_cfg.pre_check_dir)
        pre_count = len(_list_pre_check_files(pre_dir)) if pre_dir.exists() else 0
        sorted_out_count = len(_list_sorted_out_files(_sorted_out_dirs()))
        return templates.TemplateResponse(request, "dashboard.html", {
            "status": _service_status(),
            "stats": _dir_stats(namer_config),
            "reports": _recent_reports(report_output_dir),
            "pre_check_count": pre_count,
            "pre_check_dir": str(pre_dir),
            "sorted_out_count": sorted_out_count,
        })

    # ── Service control ──────────────────────────────────────────────────────

    @app.post("/service/{action}")
    async def service_action(action: str):
        if action in ("start", "stop", "restart"):
            subprocess.run(["systemctl", action, _SERVICE], capture_output=True)
        return RedirectResponse("/", status_code=303)

    @app.post("/work/clear")
    async def work_clear():
        try:
            paths = read_namer_paths(namer_config)
            work_dir: Path = paths.get("work_dir", Path("/var/lib/namer/work"))  # type: ignore
            watch_dir: Path = paths.get("watch_dir", Path("/var/lib/namer/watch"))  # type: ignore
            moved = 0
            skipped = 0
            for item in list(work_dir.iterdir()):
                if not item.is_file():
                    continue
                ok, _ = _move_to_directory(item, watch_dir)
                if ok:
                    moved += 1
                else:
                    skipped += 1
            return {"ok": True, "moved": moved, "skipped": skipped}
        except Exception as e:
            return {"ok": False, "error": str(e)}

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
                    if f.is_file() and not _is_ignored_file(f)
                ],
                key=lambda x: x["name"],
            )
        return templates.TemplateResponse(request, "files.html", {
            "dir_name": dir_name,
            "files": files,
        })

    # ── Failed files with actions ────────────────────────────────────────────

    @app.get("/failed", response_class=HTMLResponse)
    async def failed_list(request: Request, page: int = 1, per_page: int = _DEFAULT_PER_PAGE):
        try:
            paths = read_namer_paths(namer_config)
            failed_dir = paths["failed_dir"]
        except Exception:
            failed_dir = Path("/var/lib/namer/failed")
        pg = _paginate(_list_failed_files(failed_dir), page, per_page)
        return templates.TemplateResponse(request, "failed.html", {
            "files": pg["slice"],
            "pg": pg,
            "page_url": "/failed",
            "retry_time": _read_cfg_value(namer_config, "retry_time"),
        })

    def _sorted_out_dirs() -> list[Path]:
        dirs: list[Path] = []
        # Configured target first (e.g. NAS), then the default locations so
        # files moved there before configuration stay visible/restorable.
        custom = (load_ai_config(helper_config_dir).aussortiert_dir or "").strip()
        if custom:
            dirs.append(Path(custom))
        try:
            dirs.append(_get_pre_check_dir().parent / "aussortiert")
        except Exception:
            pass
        try:
            dirs.append(read_namer_paths(namer_config)["failed_dir"].parent / "aussortiert")
        except Exception:
            pass
        # dedup preserving order
        out, seen = [], set()
        for d in dirs:
            k = str(d)
            if k not in seen:
                seen.add(k)
                out.append(d)
        return out

    @app.get("/aussortiert", response_class=HTMLResponse)
    async def sorted_out_page(request: Request):
        dirs = _sorted_out_dirs()
        files = _list_sorted_out_files(dirs)
        total_mb = round(sum(f["size_mb"] for f in files), 1)
        return templates.TemplateResponse(request, "aussortiert.html", {
            "files": files,
            "total_mb": total_mb,
            "dirs": [str(d) for d in dirs],
        })

    @app.post("/aussortiert/restore")
    async def sorted_out_restore(name: str, target: str = "pre-check"):
        """Datei aus aussortiert/ zurück nach pre-check/ oder failed/ holen."""
        try:
            src = None
            for d in _sorted_out_dirs():
                cand = _safe_path(d, name)
                if cand and cand.exists():
                    src = cand
                    break
            if src is None:
                return {"ok": False, "error": "Datei nicht gefunden"}
            if target == "failed":
                dest_dir = read_namer_paths(namer_config)["failed_dir"]
            else:
                dest_dir = _get_pre_check_dir()
            ok, error = _move_to_directory(src, dest_dir)
            if not ok:
                return {"ok": False, "error": error or "Verschieben fehlgeschlagen"}
            # Sidecar mitnehmen falls vorhanden
            stem = Path(name).stem
            for d in _sorted_out_dirs():
                sc = _safe_path(d, f"{stem}_namer.json.gz")
                if sc and sc.exists():
                    _move_to_directory(sc, dest_dir)
                    break
            return {"ok": True, "restored_to": str(dest_dir / src.name)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/namer/retry-time")
    async def set_retry_time(request: Request):
        try:
            body = await request.json()
            value = body.get("value", "").strip()
            if value and not value.isdigit():
                return {"ok": False, "error": "retry_time muss eine Zahl sein"}
            ok = _write_cfg_value(namer_config, "retry_time", value)
            return {"ok": ok}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/failed/retry-all")
    async def failed_retry_all():
        try:
            paths = read_namer_paths(namer_config)
            failed_dir = paths["failed_dir"]
            watch_dir = paths["watch_dir"]
            count = 0
            skipped = 0
            for video in failed_dir.rglob("*"):
                try:
                    if not video.is_file():
                        continue
                    if video.suffix.lower() not in _VIDEO_EXTS:
                        continue
                    if _is_ignored_file(video):
                        continue
                    ok, _ = _move_to_directory(video, watch_dir)
                    if not ok:
                        skipped += 1
                        continue
                    json_gz = _safe_path(failed_dir, f"{video.stem}_namer.json.gz")
                    if json_gz and json_gz.exists():
                        try:
                            json_gz.unlink()
                        except OSError:
                            pass
                    count += 1
                except OSError:
                    skipped += 1
            return {"ok": True, "count": count, "skipped": skipped}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/failed/retry")
    async def failed_retry(name: str):
        try:
            paths = read_namer_paths(namer_config)
            failed_dir = paths["failed_dir"]
            watch_dir = paths["watch_dir"]
            video = _safe_path(failed_dir, name)
            if video and video.exists():
                ok, _ = _move_to_directory(video, watch_dir)
                if not ok:
                    return RedirectResponse("/failed", status_code=303)
                stem = Path(name).stem
                json_gz = _safe_path(failed_dir, f"{stem}_namer.json.gz")
                if json_gz and json_gz.exists():
                    json_gz.unlink()
        except Exception:
            pass
        return RedirectResponse("/failed", status_code=303)

    @app.post("/failed/rename")
    async def failed_rename(name: str, new_name: str):
        try:
            try:
                paths = read_namer_paths(namer_config)
                failed_dir = paths["failed_dir"]
            except Exception:
                failed_dir = Path("/var/lib/namer/failed")

            src = _safe_path(failed_dir, name)
            if not src or not src.exists():
                return {"ok": False, "error": "Datei nicht gefunden"}

            # Keep original extension if suggestion omits it
            suggested = new_name.strip()
            if Path(suggested).suffix.lower() not in _VIDEO_EXTS:
                suggested = suggested + src.suffix

            dst = _safe_path(failed_dir, suggested)
            if not dst:
                return {"ok": False, "error": "Ungültiger Dateiname"}
            if dst.exists():
                return {"ok": False, "error": "Datei mit diesem Namen existiert bereits"}

            src.rename(dst)

            old_sidecar = _safe_path(failed_dir, f"{src.stem}_namer.json.gz")
            if old_sidecar and old_sidecar.exists():
                new_sidecar = _safe_path(failed_dir, f"{dst.stem}_namer.json.gz")
                if new_sidecar:
                    try:
                        old_sidecar.rename(new_sidecar)
                    except OSError:
                        pass

            return {"ok": True, "new_name": dst.name}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/failed/move")
    async def failed_move(name: str):
        """Aussortieren: failed/ → aussortiert/ (Video + Sidecar). Verb: set_aside."""
        try:
            failed_dir = read_namer_paths(namer_config)["failed_dir"]
            ok, result = _act_set_aside(name, source_dir=failed_dir, move_sidecar=True)
            if not ok:
                return {"ok": False, "error": result}
            return {"ok": True, "moved_to": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

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

    # ── Proxmox SSH integration ──────────────────────────────────────────────

    @app.get("/proxmox", response_class=HTMLResponse)
    async def proxmox_page(request: Request):
        cfg = load_proxmox_config(helper_config_dir)
        mounts = load_mounts(helper_config_dir)
        nfs_mounts = [m for m in mounts if m.protocol == "nfs"]
        pubkey = ensure_ssh_key()
        return templates.TemplateResponse(request, "proxmox.html", {
            "cfg": cfg,
            "pubkey": pubkey,
            "nfs_mounts": nfs_mounts,
        })

    @app.post("/proxmox/config")
    async def proxmox_config_save(request: Request):
        try:
            body = await request.json()
            cfg = ProxmoxConfig(
                host=body.get("host", "").strip(),
                user=body.get("user", "root").strip() or "root",
                port=int(body.get("port", 22)),
                lxc_id=_clean_lxc_id(body.get("lxc_id", "103")),
            )
            save_proxmox_config(helper_config_dir, cfg)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/proxmox/test")
    async def proxmox_test(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if body.get("host"):
            cfg = ProxmoxConfig(
                host=body["host"].strip(),
                user=body.get("user", "root").strip() or "root",
                port=int(body.get("port", 22)),
                lxc_id=_clean_lxc_id(body.get("lxc_id", "103")),
            )
        else:
            cfg = load_proxmox_config(helper_config_dir)
        if not cfg.host:
            return {"ok": False, "output": "Kein Host konfiguriert"}
        ok, out = run_remote(cfg, "echo OK && hostname")
        return {"ok": ok, "output": out}

    @app.post("/proxmox/host-mount")
    async def proxmox_host_mount(mount_id: str):
        cfg = load_proxmox_config(helper_config_dir)
        if not cfg.host:
            return {"ok": False, "error": "Proxmox SSH nicht konfiguriert"}
        mounts = load_mounts(helper_config_dir)
        mount = next((m for m in mounts if m.id == mount_id), None)
        if not mount:
            return {"ok": False, "error": "Mount nicht gefunden"}
        steps = setup_host_mount(cfg, mount.host, mount.share, mount.target)
        all_ok = all(ok for _, ok, _ in steps)
        return {
            "ok": all_ok,
            "steps": [{"desc": d, "ok": ok, "out": out} for d, ok, out in steps],
        }

    @app.post("/proxmox/host-unmount")
    async def proxmox_host_unmount(mount_id: str):
        cfg = load_proxmox_config(helper_config_dir)
        if not cfg.host:
            return {"ok": False, "error": "Proxmox SSH nicht konfiguriert"}
        mounts = load_mounts(helper_config_dir)
        mount = next((m for m in mounts if m.id == mount_id), None)
        if not mount:
            return {"ok": False, "error": "Mount nicht gefunden"}
        steps = teardown_host_mount(cfg, mount.host, mount.share, mount.target)
        all_ok = all(ok for _, ok, _ in steps)
        return {
            "ok": all_ok,
            "steps": [{"desc": d, "ok": ok, "out": out} for d, ok, out in steps],
        }

    @app.post("/proxmox/restart")
    async def proxmox_restart():
        cfg = load_proxmox_config(helper_config_dir)
        if not cfg.host:
            return {"ok": False, "output": "Proxmox SSH nicht konfiguriert"}
        ok, out = run_remote(cfg, f"pct restart {_clean_lxc_id(cfg.lxc_id)}", timeout=30)
        return {"ok": ok, "output": out}

    # ── Pre-Check directory ───────────────────────────────────────────────────

    def _get_pre_check_dir() -> Path:
        return Path(load_ai_config(helper_config_dir).pre_check_dir)

    def _aussortiert_dir_for(default_parent: Path) -> Path:
        """Wohin aussortiert wird: konfigurierter Pfad (z.B. NAS) oder Default.

        Leer in den Settings → <default_parent>/aussortiert (lokal, klein).
        Gesetzt → der konfigurierte Pfad (z.B. /mnt/nas/aussortiert), damit
        die kleine Container-Disk nicht vollläuft.
        """
        custom = (load_ai_config(helper_config_dir).aussortiert_dir or "").strip()
        return Path(custom) if custom else default_parent / "aussortiert"

    def _find_dest_duplicate(oshash: str, file_size: int) -> str | None:
        """Check dest/ for a file with the same oshash. Fast: pre-filters by size."""
        try:
            from namer_helper.namer_bridge.hasher import compute_oshash as _co
            paths = read_namer_paths(namer_config)
            dest_dir: Path | None = paths.get("dest_dir")  # type: ignore[assignment]
            if not dest_dir or not dest_dir.exists():
                return None
            for f in dest_dir.rglob("*"):
                if not f.is_file():
                    continue
                try:
                    if f.stat().st_size != file_size:
                        continue
                except OSError:
                    continue
                if _co(f) == oshash:
                    return str(f.relative_to(dest_dir))
        except Exception:
            pass
        return None

    def _format_duration(seconds: int | None) -> str:
        if not seconds:
            return "—"
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _pre_check_relative_name(pre_dir: Path, path: Path) -> str:
        try:
            return path.relative_to(pre_dir).as_posix()
        except ValueError:
            return path.name

    def _list_pre_check_files(pre_dir: Path) -> list[dict]:
        if not pre_dir.exists():
            return []
        items = []
        for f in sorted(pre_dir.rglob("*")):
            if not f.is_file():
                continue
            if f.suffix.lower() not in _VIDEO_EXTS:
                continue
            if _is_ignored_file(f):
                continue
            try:
                size_bytes = f.stat().st_size
                size_mb = round(size_bytes / 1_048_576, 1)
            except OSError:
                size_bytes = 0
                size_mb = 0
            try:
                from namer_helper.web import metadata_cache

                cached_meta = metadata_cache.get(f) or {}
            except Exception:
                cached_meta = {}
            duration_seconds = int(cached_meta.get("duration_seconds") or 0)
            processed = bool(cached_meta.get("processed"))
            lookup_cached = False
            try:
                from namer_helper.namer_bridge.hasher import compute_oshash
                from namer_helper.web import lookup_cache, metadata_cache

                oshash = str(cached_meta.get("oshash") or "")
                if not oshash:
                    oshash = compute_oshash(f) or ""
                    if oshash:
                        updated_meta = dict(cached_meta)
                        updated_meta["oshash"] = oshash
                        metadata_cache.set(f, updated_meta)
                lookup_cached = bool(oshash and lookup_cache.get(oshash))
            except Exception:
                lookup_cached = False
            rel_name = _pre_check_relative_name(pre_dir, f)
            parent = str(Path(rel_name).parent)
            folder = "" if parent == "." else parent
            items.append({
                "name": rel_name,
                "basename": f.name,
                "folder": folder,
                "name_encoded": quote(rel_name, safe=""),
                "size_mb": size_mb,
                "size_bytes": size_bytes,
                "duration_seconds": duration_seconds,
                "duration_hms": _format_duration(duration_seconds),
                "duration_cached": bool(duration_seconds),
                "processed": processed,
                "lookup_cached": lookup_cached,
            })
        return items

    def _build_name_from_stashdb(scene: object, original_ext: str) -> str:
        parts = []
        if scene.studio:  # type: ignore[attr-defined]
            parts.append(scene.studio)  # type: ignore[attr-defined]
        if scene.date:  # type: ignore[attr-defined]
            parts.append(scene.date)  # type: ignore[attr-defined]
        parts.append(scene.title or "Unknown")  # type: ignore[attr-defined]
        name = " - ".join(parts)
        if scene.performers:  # type: ignore[attr-defined]
            perf = ", ".join(scene.performers[:3])  # type: ignore[attr-defined]
            name += f" ({perf})"
        name = re.sub(r'[<>:"/\\|?*]', "", name).strip()
        return name + original_ext

    @app.get("/pre-check", response_class=HTMLResponse)
    async def pre_check_list(request: Request, page: int = 1, per_page: int = _DEFAULT_PER_PAGE):
        pre_dir = _get_pre_check_dir()
        pg = _paginate(_list_pre_check_files(pre_dir), page, per_page)
        return templates.TemplateResponse(request, "pre-check.html", {
            "files": pg["slice"],
            "pg": pg,
            "page_url": "/pre-check",
            "dir_exists": pre_dir.exists(),
            "pre_check_dir": str(pre_dir),
        })


    @app.get("/pre-check/duration")
    async def pre_check_duration(name: str):
        ai_cfg = load_ai_config(helper_config_dir)
        pre_dir = Path(ai_cfg.pre_check_dir)
        video_path = _safe_path(pre_dir, name)
        if video_path is None or not video_path.exists():
            return {"ok": False, "error": "Datei nicht gefunden", "duration_seconds": 0, "duration_hms": "—"}
        try:
            from namer_helper.namer_bridge.hasher import get_video_info
            from namer_helper.web import metadata_cache

            cached_meta = metadata_cache.get(video_path) or {}
            cached_seconds = int(cached_meta.get("duration_seconds") or 0)
            if cached_seconds:
                return {"ok": True, "cached": True, "duration_seconds": cached_seconds, "duration_hms": _format_duration(cached_seconds)}

            loop = asyncio.get_running_loop()
            info = await loop.run_in_executor(None, get_video_info, video_path)
            seconds = int(info.get("duration") or 0)
            updated_meta = dict(cached_meta)
            updated_meta["duration_seconds"] = seconds
            metadata_cache.set(video_path, updated_meta)
            return {"ok": True, "cached": False, "duration_seconds": seconds, "duration_hms": _format_duration(seconds)}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "duration_seconds": 0, "duration_hms": "—"}

    @app.post("/pre-check/create-dir")
    async def pre_check_create_dir():
        try:
            _get_pre_check_dir().mkdir(parents=True, exist_ok=True)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/pre-check/lookup")
    async def pre_check_lookup_endpoint(name: str):
        """HTTP-Endpunkt: einzelner Lookup mit 120 s Browser-Timeout-Schutz."""
        try:
            return await asyncio.wait_for(
                pre_check_lookup(name), timeout=_SINGLE_LOOKUP_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "timeout": True,
                "error": (
                    f"Analyse-Timeout nach {_SINGLE_LOOKUP_TIMEOUT_SECONDS}s — "
                    "Datei möglicherweise zu groß oder Server ausgelastet"
                ),
                "hashes": {"phash": None, "oshash": None, "duration": None},
                "identification": {
                    "status": "unknown", "confidence": 0.0, "source": "timeout",
                    "reason": "Timeout", "suggested_name": None,
                    "action": "review", "signals": [],
                },
                "stashdb_scenes": [], "stashdb_error": "Nicht abgeschlossen wegen Gesamt-Timeout", "stashdb_suggested": None,
                "tpdb_scenes": [], "tpdb_error": "Nicht abgeschlossen wegen Gesamt-Timeout", "tpdb_suggested": None,
                "tpdb_movies": [], "tpdb_movie_error": "Nicht abgeschlossen wegen Gesamt-Timeout", "tpdb_movie_suggested": None,
                "ollama": {
                    "cleaned_name": "",
                    "search_queries": [],
                    "confidence": 0.0,
                    "recommended_action": "manual_review",
                    "reason": "",
                    "error": "Nicht abgeschlossen wegen Gesamt-Timeout",
                },
                "filename_parsed": None,
                "jav_code": None, "tpdb_crosscheck": "skipped",
            }

    async def pre_check_lookup(name: str):
        try:
            from namer_helper.namer_bridge.filename_parser import parse_filename
            from namer_helper.namer_bridge.hasher import compute_oshash, compute_phash, detect_studio_logo, extract_frame_text, get_video_info
            from namer_helper.jav import detect as detect_jav
            from namer_helper.aliases import load as load_aliases
            from namer_helper.ollama_bridge.analyzer import analyze_filename
            from namer_helper.ollama_bridge.client import OllamaClient
            from namer_helper.stash_bridge.stashdb import StashDBClient
            from namer_helper.stash_bridge.theporndb import ThePornDBClient
            from namer_helper.web import lookup_cache

            loop = asyncio.get_running_loop()
            ai_cfg = load_ai_config(helper_config_dir)
            pre_dir = Path(ai_cfg.pre_check_dir)
            source_name = Path(name).name
            # Runtime aliases: /etc/namer-helper/aliases.json (same path learn() writes to).
            # Falls back to package defaults if the file doesn't exist yet.
            aliases = load_aliases(helper_config_dir / "aliases.json")
            from namer_helper import vocabulary as _vocab
            vocab = _vocab.load(helper_config_dir)
            parsed = parse_filename(source_name, aliases=aliases, vocabulary=vocab)
            jav_code = detect_jav(source_name)
            ext = Path(source_name).suffix
            hashes: dict = {"phash": None, "oshash": None, "duration": None, "resolution": None, "ocr_text": ""}
            video_path = _safe_path(pre_dir, name)
            has_video = bool(video_path and video_path.exists())

            # ── Phase 1 (parallel): oshash + vinfo ──
            if has_video:
                oshash_val, vinfo = await asyncio.gather(
                    loop.run_in_executor(None, compute_oshash, video_path),
                    loop.run_in_executor(None, get_video_info, video_path),
                )
                hashes["oshash"] = oshash_val
                if vinfo.get("duration"):
                    hashes["duration"] = vinfo["duration"]
                if vinfo.get("resolution_label"):
                    hashes["resolution"] = vinfo["resolution_label"]
                if vinfo.get("meta_title"):
                    hashes["meta_title"] = vinfo["meta_title"]
                if vinfo.get("meta_studio"):
                    hashes["meta_studio"] = vinfo["meta_studio"]
                if vinfo.get("meta_date"):
                    hashes["meta_date"] = vinfo["meta_date"]
                if vinfo.get("meta_performers"):
                    hashes["meta_performers"] = vinfo["meta_performers"]
                if vinfo.get("meta_copyright"):
                    hashes["meta_copyright"] = vinfo["meta_copyright"]
                if vinfo.get("meta_encoded_by"):
                    hashes["meta_encoded_by"] = vinfo["meta_encoded_by"]
            else:
                vinfo = {}

            oshash = hashes["oshash"]
            duration = hashes.get("duration")
            if has_video and oshash:
                try:
                    from namer_helper.web import metadata_cache

                    cached_meta = metadata_cache.get(video_path) or {}
                    updated_meta = dict(cached_meta)
                    updated_meta["oshash"] = oshash
                    metadata_cache.set(video_path, updated_meta)
                except Exception:
                    pass

            # Rule check — user-confirmed decisions, highest priority
            if oshash:
                try:
                    from namer_helper.rules import load_rules, match_by_hash, rule_to_identification
                    _rules = load_rules(helper_config_dir / "rules.yaml")
                    _rule = match_by_hash(oshash, _rules)
                    if _rule is not None:
                        return {
                            "ok": True,
                            "hashes": hashes,
                            "filename_parsed": None,
                            "identification": rule_to_identification(_rule, source_name),
                            "stashdb_scenes": [], "stashdb_error": None,
                            "stashdb_suggested": None,
                            "tpdb_scenes": [], "tpdb_error": None, "tpdb_suggested": None,
                            "tpdb_movies": [], "tpdb_movie_error": None,
                            "tpdb_movie_suggested": None,
                            "ollama": None, "jav_code": None,
                            "tpdb_crosscheck": "skipped",
                        }
                except Exception:
                    pass  # rule check failure must never block the pipeline

            # Cache check (needs oshash)
            if oshash:
                cached = lookup_cache.get(oshash)
                if cached is not None:
                    return cached

            # ── Phase 2 (parallel): phash + OCR + Ollama ──
            def _phash():
                return compute_phash(video_path, duration=duration) if has_video else None

            def _ocr():
                return extract_frame_text(video_path, duration=duration) if has_video else ""

            def _ollama():
                if not ai_cfg.ollama_url:
                    return None
                try:
                    client = OllamaClient(base_url=ai_cfg.ollama_url)
                    if not client.is_available():
                        return None
                    # Pass all known signals so Ollama can build a meaningful suggestion
                    # even when the filename is a tag list or garbage.
                    res = analyze_filename(
                        parsed.cleaned or source_name,
                        client,
                        model=ai_cfg.ollama_model,
                        performers=parsed.performers or [],
                        studio=parsed.studio,
                        date=parsed.date,
                        duration=duration,
                        logo_studio=hashes.get("logo_studio") or "",
                    )
                    return {
                        "cleaned_name": res.cleaned_name,
                        "search_queries": res.search_queries,
                        "confidence": res.confidence,
                        "recommended_action": res.recommended_action,
                        "reason": res.reason,
                        "error": res.error,
                    }
                except Exception:
                    return None

            def _dup_check():
                if not (oshash and has_video):
                    return None
                try:
                    return _find_dest_duplicate(oshash, video_path.stat().st_size)
                except Exception:
                    return None

            def _logo():
                if not (has_video and ai_cfg.ollama_url):
                    return None
                if not _is_moondream_available(ai_cfg.ollama_url):
                    return None
                try:
                    return detect_studio_logo(video_path, ai_cfg.ollama_url, model="moondream")
                except Exception:
                    return None

            phash_val, ocr_val, ollama_result, dest_duplicate, logo_studio = await asyncio.gather(
                loop.run_in_executor(None, _phash),
                loop.run_in_executor(None, _ocr),
                loop.run_in_executor(None, _ollama),
                loop.run_in_executor(None, _dup_check),
                loop.run_in_executor(None, _logo),
            )
            hashes["phash"] = phash_val
            hashes["ocr_text"] = ocr_val or ""
            hashes["logo_studio"] = logo_studio or ""
            phash = phash_val

            # ── Phase 3 (parallel): StashDB fingerprint + StashDB performer + TPDB fingerprint ──
            def _stashdb():
                if not (oshash or phash):
                    return None
                return StashDBClient(api_key=ai_cfg.stashdb_api_key).query_by_fingerprints(
                    oshash=oshash, phash=phash
                )

            def _stashdb_performer():
                known = parsed.performers or []
                if not known:
                    return None
                ctx_dur = hashes.get("duration")
                ctx_stu = parsed.studio or vinfo.get("meta_studio") or vinfo.get("meta_copyright")
                ctx_dt  = parsed.date or vinfo.get("meta_date")
                return StashDBClient(api_key=ai_cfg.stashdb_api_key).search_by_performer(
                    known, studio=ctx_stu, date=ctx_dt, duration=ctx_dur
                )

            def _stashdb_context():
                ctx_dur = hashes.get("duration")
                ctx_stu = parsed.studio or vinfo.get("meta_studio") or vinfo.get("meta_copyright")
                ctx_dt  = parsed.date or vinfo.get("meta_date")
                return StashDBClient(api_key=ai_cfg.stashdb_api_key).search_by_context(
                    title=parsed.cleaned or "",
                    performers=parsed.performers or [],
                    studio=ctx_stu,
                    date=ctx_dt,
                    duration=ctx_dur,
                )

            def _tpdb_fp():
                if not (oshash or phash):
                    return None
                tpdb_key = ai_cfg.theporndb_api_key or read_namer_porndb_token(namer_config)
                return ThePornDBClient(api_key=tpdb_key).query_by_fingerprints(
                    oshash=oshash, phash=phash
                )

            def _tpdb_movie_fp():
                if not (oshash or phash):
                    return None
                tpdb_key = ai_cfg.theporndb_api_key or read_namer_porndb_token(namer_config)
                return ThePornDBClient(api_key=tpdb_key).query_movies_by_hashes(
                    oshash=oshash, phash=phash
                )

            def _tpdb_jav():
                if not jav_code:
                    return None
                tpdb_key = ai_cfg.theporndb_api_key or read_namer_porndb_token(namer_config)
                return ThePornDBClient(api_key=tpdb_key).search_jav_by_code(jav_code.code)

            sdb_result, sdb_performer_result, sdb_context_result, tpdb_fp_result, tpdb_movie_fp_result, tpdb_jav_result = await asyncio.gather(
                loop.run_in_executor(None, _stashdb),
                loop.run_in_executor(None, _stashdb_performer),
                loop.run_in_executor(None, _stashdb_context),
                loop.run_in_executor(None, _tpdb_fp),
                loop.run_in_executor(None, _tpdb_movie_fp),
                loop.run_in_executor(None, _tpdb_jav),
            )

            # Merge StashDB results — priority: fingerprint > context search > performer lookup
            # Context search is scored so it's more reliable than performer-only lookup
            stashdb_scenes: list[dict] = []
            stashdb_error: str | None = None
            stashdb_suggested: str | None = None
            if sdb_result and sdb_result.found:
                effective_sdb = sdb_result
            elif sdb_context_result and sdb_context_result.found:
                effective_sdb = sdb_context_result
            else:
                effective_sdb = sdb_performer_result

            if effective_sdb is not None:
                stashdb_error = effective_sdb.error
                stashdb_scenes = [
                    {
                        "title": s.title,
                        "date": s.date,
                        "studio": s.studio,
                        "performers": s.performers,
                        "url": s.stashdb_url,
                        "duration": s.duration,
                        "match_via": (
                            "fingerprint" if (sdb_result and sdb_result.found)
                            else "context" if (sdb_context_result and sdb_context_result.found)
                            else "performer"
                        ),
                    }
                    for s in effective_sdb.scenes
                ]
                if effective_sdb.best:
                    stashdb_suggested = _build_name_from_stashdb(effective_sdb.best, ext)
            else:
                stashdb_error = "Datei nicht gefunden oder zu klein für Hash"

            # ── Phase 4: TPDB context searches (sequential, need StashDB + Ollama) ──
            ollama_confident = (
                ollama_result
                and not ollama_result.get("error")
                and (ollama_result.get("confidence") or 0) >= 0.7
            )
            has_primary_result = bool(stashdb_scenes) or ollama_confident

            tpdb_scenes: list[dict] = []
            tpdb_error: str | None = None
            tpdb_suggested: str | None = None
            tpdb_match_method: str = "hash"
            tpdb_result = tpdb_fp_result  # fingerprint result from Phase 3
            if not (tpdb_result and tpdb_result.found) and tpdb_jav_result and tpdb_jav_result.found:
                tpdb_result = tpdb_jav_result
            tpdb_movies: list[dict] = []
            tpdb_movie_error: str | None = None
            tpdb_movie_suggested: str | None = None
            tpdb_movie_match_method: str = "hash"
            tpdb_movie_result = tpdb_movie_fp_result

            # If parsed.studio looks like a performer list, recover performers from it
            import re as _re
            if parsed.studio and not parsed.performers:
                _studio_parts = [p.strip() for p in _re.split(r',|&|\band\b', parsed.studio, flags=_re.IGNORECASE) if p.strip()]
                if len(_studio_parts) >= 2 and all(1 <= len(p.split()) <= 3 and p[0].isupper() for p in _studio_parts):
                    parsed.performers = _studio_parts
                    parsed.studio = None

            if has_primary_result or jav_code or (tpdb_result and tpdb_result.found) or (tpdb_movie_result and tpdb_movie_result.found) or parsed.performers:
                tpdb_key = ai_cfg.theporndb_api_key or read_namer_porndb_token(namer_config)
                tpdb = ThePornDBClient(api_key=tpdb_key)

                sdb = stashdb_scenes[0] if stashdb_scenes else {}
                # Only trust StashDB context when it came from a fingerprint match.
                # Performer-lookup results may be from a different scene entirely —
                # using their performers/studio/date as context inflates wrong TPDB scores.
                sdb_via_fp = sdb.get("match_via", "fingerprint") == "fingerprint"
                sdb_perfs   = (sdb.get("performers") or []) if sdb_via_fp else []
                sdb_studio  = sdb.get("studio")              if sdb_via_fp else None
                sdb_date    = sdb.get("date")                if sdb_via_fp else None
                sdb_dur     = sdb.get("duration")            if sdb_via_fp else None

                meta_perfs_raw = vinfo.get("meta_performers", "")
                meta_perfs = [p.strip() for p in meta_perfs_raw.split(",") if p.strip()] if meta_perfs_raw else []

                # filename parser is the most reliable source for performers
                _seen: set[str] = set()
                ctx_performers: list[str] = []
                for p in (parsed.performers + sdb_perfs + meta_perfs):
                    key = p.lower().strip()
                    if len(key) < 3:
                        continue
                    if key and key not in _seen:
                        _seen.add(key)
                        ctx_performers.append(p)

                # Logo detection is diagnostic only. Corner logos can be overlays,
                # ads, trailers, or unrelated watermarks, so do not use them to bias DB search.
                ctx_studio = (sdb_studio or parsed.studio or
                              vinfo.get("meta_studio") or vinfo.get("meta_copyright"))
                ctx_date = sdb_date or parsed.date or vinfo.get("meta_date")
                ctx_duration = sdb_dur or hashes.get("duration")

                # Movie lookup: full-length files often live under /movies, not /scenes.
                if not (tpdb_movie_result and tpdb_movie_result.found):
                    movie_terms = [parsed.cleaned or ""]
                    if stashdb_scenes:
                        movie_terms.append(sdb.get("title", ""))
                    if ollama_result:
                        movie_terms.extend((ollama_result.get("search_queries") or [])[:2])
                    for mt in movie_terms:
                        if not mt:
                            continue
                        tpdb_movie_result = tpdb.search_movies_by_context(
                            mt, performers=ctx_performers, studio=ctx_studio,
                            date=ctx_date, duration=ctx_duration,
                        )
                        if tpdb_movie_result and tpdb_movie_result.found:
                            break

                # Step 1.5: Performer-Datenbanksuche — alle bekannten Performer als primäres Signal
                if (not tpdb_result or not tpdb_result.found) and ctx_performers:
                    tpdb_result = tpdb.search_by_performer(
                        ctx_performers,
                        studio=ctx_studio,
                        date=ctx_date,
                        duration=ctx_duration,
                    )

                # Step 2: StashDB title search
                if (not tpdb_result or not tpdb_result.found) and stashdb_scenes:
                    search_term = sdb.get("title", "")
                    if search_term:
                        tpdb_result = tpdb.search_by_context(
                            search_term,
                            performers=ctx_performers, studio=ctx_studio,
                            date=ctx_date, duration=ctx_duration,
                        )

                # Step 3: Ollama search queries
                if (not tpdb_result or not tpdb_result.found) and ollama_result:
                    for q in (ollama_result.get("search_queries") or [])[:2]:
                        tpdb_result = tpdb.search_by_context(
                            q,
                            performers=ctx_performers, studio=ctx_studio,
                            date=ctx_date, duration=ctx_duration,
                        )
                        if tpdb_result and tpdb_result.found:
                            break

                # Step 4: OCR last-resort
                ocr_text = hashes.get("ocr_text", "")
                if (not tpdb_result or not tpdb_result.found) and ocr_text:
                    for line in ocr_text.splitlines()[:3]:
                        if len(line.split()) >= 2:
                            tpdb_result = tpdb.search_by_context(
                                line,
                                performers=ctx_performers, studio=ctx_studio,
                                date=ctx_date, duration=ctx_duration,
                            )
                            if tpdb_result and tpdb_result.found:
                                break

                # Step 5: Optional semantic fallback over a local ChromaDB index.
                # This is disabled automatically when chromadb or nomic-embed-text is absent.
                if (not tpdb_result or not tpdb_result.found) and ai_cfg.ollama_url:
                    try:
                        from namer_helper.embedding import search_scene_index

                        semantic_terms = [parsed.cleaned or ""]
                        if ollama_result:
                            semantic_terms.extend((ollama_result.get("search_queries") or [])[:2])
                        if ocr_text:
                            semantic_terms.extend([line for line in ocr_text.splitlines()[:2] if line.strip()])
                        for term in semantic_terms:
                            if not term.strip():
                                continue
                            emb_result = search_scene_index(
                                term,
                                ollama_url=ai_cfg.ollama_url,
                                persist_dir=helper_config_dir / "embeddings",
                                min_score=0.35,
                            )
                            if emb_result.found:
                                tpdb_result = ThePornDBResult(
                                    scenes=[
                                        ThePornDBScene(
                                            id=hit.id,
                                            title=hit.title,
                                            date=hit.metadata.get("date"),
                                            site=hit.metadata.get("site"),
                                            network=hit.metadata.get("network"),
                                            performers=[
                                                p.strip()
                                                for p in str(hit.metadata.get("performers") or "").split(",")
                                                if p.strip()
                                            ],
                                            url=hit.metadata.get("url") or "",
                                            image=hit.metadata.get("image") or "",
                                            match_method="embedding",
                                            score=int(round(hit.score * 100)),
                                            score_breakdown={"Embedding": int(round(hit.score * 100))},
                                            duration=int(hit.metadata["duration"]) if hit.metadata.get("duration") else None,
                                            sku=hit.metadata.get("sku"),
                                        )
                                        for hit in emb_result.hits
                                    ],
                                    match_method="embedding",
                                )
                                break
                    except Exception:
                        pass

                if tpdb_result:
                    tpdb_error = tpdb_result.error
                    tpdb_match_method = tpdb_result.match_method
                    tpdb_scenes = [
                        {
                            "title": s.title,
                            "date": s.date,
                            "site": s.site,
                            "network": s.network,
                            "performers": s.performers,
                            "url": s.url,
                            "image": s.image,
                            "match_method": s.match_method,
                            "score": s.score,
                            "score_breakdown": s.score_breakdown,
                            "duration": s.duration,
                            "sku": s.sku,
                        }
                        for s in tpdb_result.scenes
                    ]
                    if tpdb_result.best:
                        b = tpdb_result.best
                        if b.match_method in {"hash", "jav"} or b.score >= 50:
                            parts = [p for p in [b.site or b.network, b.date, b.title] if p]
                            tname = " - ".join(parts)
                            if b.performers:
                                tname += f" ({', '.join(b.performers[:3])})"
                            tpdb_suggested = re.sub(r'[<>:"/\\|?*]', "", tname).strip() + ext

                if tpdb_movie_result:
                    tpdb_movie_error = tpdb_movie_result.error
                    tpdb_movie_match_method = tpdb_movie_result.match_method
                    tpdb_movies = [
                        {
                            "title": m.title,
                            "date": m.date,
                            "site": m.site,
                            "network": m.network,
                            "performers": m.performers,
                            "url": m.url,
                            "image": m.image,
                            "match_method": m.match_method,
                            "score": m.score,
                            "score_breakdown": m.score_breakdown,
                            "duration": m.duration,
                            "type": m.type,
                        }
                        for m in tpdb_movie_result.movies
                    ]
                    if tpdb_movie_result.best:
                        b = tpdb_movie_result.best
                        if b.match_method == "hash" or b.score >= 50:
                            parts = [p for p in [b.site or b.network, b.date, b.title] if p]
                            tname = " - ".join(parts)
                            if b.performers:
                                tname += f" ({', '.join(b.performers[:3])})"
                            tpdb_movie_suggested = re.sub(r'[<>:"/\\|?*]', "", tname).strip() + ext

            # Cross-check: StashDB title/performers vs TPDB + filename performers
            tpdb_crosscheck: str = "skipped"
            if has_primary_result:
                if tpdb_scenes:
                    tpdb_perfs = [p.lower() for p in tpdb_scenes[0].get("performers", [])]
                    if stashdb_scenes:
                        stash_title = (stashdb_scenes[0].get("title") or "").lower()
                        tpdb_title  = (tpdb_scenes[0].get("title") or "").lower()
                        stop = {"a", "the", "and", "in", "of", "to", "my", "her", "his"}
                        overlap = (set(stash_title.split()) - stop) & (set(tpdb_title.split()) - stop)
                        stash_perfs = [p.lower() for p in stashdb_scenes[0].get("performers", [])]
                    else:
                        overlap = set()
                        stash_perfs = []
                    # Also check filename performers (most reliable source for multi-performer)
                    all_ref_perfs = stash_perfs + [p.lower() for p in parsed.performers]
                    perf_match = any(
                        any(rp in tp or tp in rp for tp in tpdb_perfs)
                        for rp in all_ref_perfs
                    )
                    # Count how many filename performers appear in TPDB result
                    filename_perfs_in_tpdb = sum(
                        1 for fp in [p.lower() for p in parsed.performers]
                        if any(fp in tp or tp in fp for tp in tpdb_perfs)
                    )
                    multi_confirmed = len(parsed.performers) >= 2 and filename_perfs_in_tpdb >= 2
                    tpdb_crosscheck = "confirmed" if (len(overlap) >= 2 or multi_confirmed or perf_match) else "mismatch"
                else:
                    tpdb_crosscheck = "not_found"

            filename_parsed = {
                "cleaned": parsed.cleaned,
                "performers": parsed.performers,
                "studio": parsed.studio,
                "date": parsed.date,
                "resolution": parsed.resolution,
                "tech_tags": parsed.tech_tags,
                "confidence": parsed.confidence,
                "jav_code": jav_code.code if jav_code else None,
            }

            # Harvest known studios/performers from real DB hits (self-learning
            # vocabulary). Best-effort — never blocks the lookup.
            try:
                harvest_studios, harvest_perfs = [], []
                for sc in (stashdb_scenes + tpdb_scenes):
                    site = sc.get("site") or sc.get("studio") or sc.get("network")
                    if site:
                        harvest_studios.append(site)
                    harvest_perfs.extend(sc.get("performers") or [])
                if harvest_studios or harvest_perfs:
                    _vocab.learn(helper_config_dir, studios=harvest_studios, performers=harvest_perfs)
            except Exception:
                pass

            identification = build_identification(
                original_name=source_name,
                stashdb_scenes=stashdb_scenes,
                stashdb_suggested=stashdb_suggested,
                tpdb_scenes=tpdb_scenes,
                tpdb_suggested=tpdb_suggested,
                tpdb_movies=tpdb_movies,
                tpdb_movie_suggested=tpdb_movie_suggested,
                ollama=ollama_result,
                filename_parsed=filename_parsed,
                dest_duplicate=dest_duplicate,
                local_duration=hashes.get("duration"),
            )
            namer_path_preview = _namer_path_preview(
                namer_config,
                original_name=source_name,
                hashes=hashes,
                filename_parsed=filename_parsed,
                tpdb_scenes=tpdb_scenes,
                tpdb_movies=tpdb_movies,
            )
            result = {
                "ok": True,
                "hashes": hashes,
                "filename_parsed": filename_parsed,
                "identification": identification,
                "namer_path_preview": namer_path_preview,
                "stashdb_scenes": stashdb_scenes,
                "stashdb_error": stashdb_error,
                "stashdb_suggested": stashdb_suggested,
                "tpdb_scenes": tpdb_scenes,
                "tpdb_error": tpdb_error,
                "tpdb_suggested": tpdb_suggested,
                "tpdb_movies": tpdb_movies,
                "tpdb_movie_error": tpdb_movie_error,
                "tpdb_movie_suggested": tpdb_movie_suggested,
                "tpdb_movie_match_method": tpdb_movie_match_method,
                "tpdb_crosscheck": tpdb_crosscheck,
                "tpdb_match_method": tpdb_match_method,
                "ollama": ollama_result,
                "dest_duplicate": dest_duplicate,
            }
            if oshash:
                if not lookup_cache.is_transient_failure(result):
                    lookup_cache.set(oshash, result)
                    result["cache_saved"] = True
            return result
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "hashes": {"phash": None, "oshash": None, "duration": None},
                "stashdb_scenes": [],
                "stashdb_error": str(exc),
                "stashdb_suggested": None,
                "tpdb_scenes": [],
                "tpdb_error": str(exc),
                "tpdb_suggested": None,
                "tpdb_movies": [],
                "tpdb_movie_error": str(exc),
                "tpdb_movie_suggested": None,
                "tpdb_movie_match_method": "hash",
                "tpdb_crosscheck": "skipped",
                "tpdb_match_method": "hash",
                "ollama": None,
                "identification": {"status": "unknown", "confidence": 0.0, "source": "none", "reason": str(exc), "suggested_name": None, "action": "review", "signals": []},
            }

    async def _run_pre_check_scan(scan_id: str, names: list[str]) -> None:
        from namer_helper.web import scan_status

        try:
            for name in names:
                state = scan_status.load()
                if state.get("scan_id") != scan_id:
                    return
                if state.get("status") not in {"running", "pause_requested"}:
                    return
                if state.get("status") == "pause_requested":
                    scan_status.set_paused(scan_id)
                    return
                scan_status.mark_running(scan_id, name)
                try:
                    result = await asyncio.wait_for(
                        pre_check_lookup(name),
                        timeout=_SCAN_ITEM_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    result = {
                        "ok": False,
                        "error": f"Lookup-Timeout nach {_SCAN_ITEM_TIMEOUT_SECONDS}s",
                        "identification": {
                            "status": "unknown",
                            "confidence": 0.0,
                            "source": "none",
                            "reason": "Lookup-Timeout",
                            "suggested_name": None,
                            "action": "review",
                            "signals": [],
                        },
                    }
                scan_status.mark_done(
                    scan_id,
                    name,
                    ok=bool(result.get("ok")),
                    error=result.get("error"),
                    identification=result.get("identification"),
                    result=result,
                )
                # Enqueue into the review queue (MVP7) — best-effort, never blocks.
                try:
                    from namer_helper import queue as review_queue
                    review_queue.enqueue(
                        helper_config_dir / "review-queue.json",
                        name=name,
                        identification=result.get("identification"),
                        oshash=(result.get("hashes") or {}).get("oshash"),
                        tpdb_id=result.get("tpdb_sku") or None,
                    )
                except Exception:
                    pass
            scan_status.finish(scan_id)
        except Exception as exc:
            scan_status.fail(scan_id, str(exc))

    @app.post("/pre-check/scan/start")
    async def pre_check_scan_start(request: Request):
        from namer_helper.web import scan_status

        body = await request.json()
        names = body.get("names") if isinstance(body, dict) else None
        if not isinstance(names, list):
            return {"ok": False, "error": "Keine Dateiliste erhalten"}
        clean_names = [unquote(str(name)) for name in names if str(name).strip()]
        if not clean_names:
            return {"ok": False, "error": "Keine Dateien ausgewählt"}
        current = scan_status.load()
        if current.get("active") or current.get("status") in {"paused", "pause_requested"}:
            return {"ok": False, "error": "Ein Scan läuft bereits", "scan": current}
        state = scan_status.start(clean_names)
        asyncio.create_task(_run_pre_check_scan(state["scan_id"], clean_names))
        return {"ok": True, "scan": state}

    @app.post("/pre-check/scan/start-all")
    async def pre_check_scan_start_all():
        """Scan EVERY file in the pre-check dir server-side — independent of the
        rendered (capped) row list. Fixes: only the first 500 were selectable."""
        from namer_helper.web import scan_status
        pre_dir = _get_pre_check_dir()
        names = [f["name"] for f in _list_pre_check_files(pre_dir)]
        if not names:
            return {"ok": False, "error": "Keine Dateien gefunden"}
        current = scan_status.load()
        if current.get("active") or current.get("status") in {"paused", "pause_requested"}:
            return {"ok": False, "error": "Ein Scan läuft bereits", "scan": current}
        state = scan_status.start(names)
        asyncio.create_task(_run_pre_check_scan(state["scan_id"], names))
        return {"ok": True, "scan": state, "count": len(names)}

    @app.post("/pre-check/scan/pause")
    async def pre_check_scan_pause():
        from namer_helper.web import scan_status

        return {"ok": True, "scan": scan_status.pause()}

    @app.post("/pre-check/scan/resume")
    async def pre_check_scan_resume():
        from namer_helper.web import scan_status

        state = scan_status.resume()
        names = scan_status.pending_names(state)
        if names and state.get("scan_id"):
            asyncio.create_task(_run_pre_check_scan(state["scan_id"], names))
        return {"ok": True, "scan": scan_status.load()}

    @app.post("/pre-check/scan/stop")
    async def pre_check_scan_stop():
        from namer_helper.web import scan_status

        return {"ok": True, "scan": scan_status.stop()}

    @app.get("/pre-check/scan/status")
    async def pre_check_scan_status():
        from namer_helper.web import scan_status

        return {"ok": True, "scan": scan_status.load()}

    @app.get("/pre-check/video")
    async def pre_check_video(name: str):
        import mimetypes
        ai_cfg = load_ai_config(helper_config_dir)
        pre_dir = Path(ai_cfg.pre_check_dir)
        video_path = _safe_path(pre_dir, name)
        if video_path is None or not video_path.exists():
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "Datei nicht gefunden"}, status_code=404)
        mime, _ = mimetypes.guess_type(str(video_path))
        return FileResponse(video_path, media_type=mime or "video/mp4")

    @app.post("/pre-check/stashdb-submit")
    async def pre_check_stashdb_submit(scene_id: str, oshash: str = "", phash: str = "", duration: int = 0):
        """Submit our file's fingerprints to StashDB for a confirmed scene match."""
        try:
            from namer_helper.stash_bridge.stashdb import StashDBClient
            ai_cfg = load_ai_config(helper_config_dir)
            sdb = StashDBClient(api_key=ai_cfg.stashdb_api_key)
            result = sdb.submit_fingerprint(
                scene_id,
                oshash=oshash or None,
                phash=phash or None,
                duration=duration or None,
            )
            return {"ok": result["submitted"] > 0, **result}
        except Exception as exc:
            return {"ok": False, "submitted": 0, "errors": [str(exc)]}

    @app.post("/pre-check/cache/invalidate")
    async def pre_check_cache_invalidate(name: str = "", oshash: str = ""):
        try:
            from namer_helper.web import lookup_cache
            if not oshash:
                from namer_helper.namer_bridge.hasher import compute_oshash

                ai_cfg = load_ai_config(helper_config_dir)
                pre_dir = Path(ai_cfg.pre_check_dir)
                video_path = _safe_path(pre_dir, name)
                if video_path is None:
                    return {"ok": False, "error": "Datei nicht gefunden"}
                oshash = compute_oshash(video_path)
            if not oshash:
                return {"ok": False, "error": "oshash konnte nicht berechnet werden"}
            removed = lookup_cache.invalidate(oshash)
            return {"ok": True, "removed": removed, "oshash": oshash}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/pre-check/cache/clear-all")
    async def pre_check_cache_clear_all():
        """Leert den gesamten Lookup-Cache → alle Dateien werden neu analysiert
        (nötig nach Logik-Fixes, da der Cache nie abläuft)."""
        try:
            from namer_helper.web import lookup_cache
            removed = lookup_cache.clear_all()
            return {"ok": True, "removed": removed}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/pre-check/rename")
    async def pre_check_rename(name: str, new_name: str, oshash: str = "", tpdb_id: str = ""):
        try:
            pre_dir = _get_pre_check_dir()
            src = _safe_path(pre_dir, name)
            if not src or not src.exists():
                return {"ok": False, "error": "Datei nicht gefunden"}
            try:
                from namer_helper.web import metadata_cache

                cached_meta = metadata_cache.get(src) or {}
            except Exception:
                metadata_cache = None  # type: ignore[assignment]
                cached_meta = {}
            if not oshash:
                try:
                    from namer_helper.namer_bridge.hasher import compute_oshash

                    oshash = compute_oshash(src) or ""
                except Exception:
                    oshash = ""
            suggested = new_name.strip()
            if Path(suggested).suffix.lower() not in _VIDEO_EXTS:
                suggested = suggested + src.suffix
            suggested_path = Path(suggested)
            if suggested_path.is_absolute() or any(part in {".", ".."} for part in suggested_path.parts):
                return {"ok": False, "error": "Ungültiger Dateiname"}
            if len(suggested_path.parts) == 1:
                dst = src.with_name(suggested)
                try:
                    dst.resolve().relative_to(pre_dir.resolve())
                except ValueError:
                    return {"ok": False, "error": "Ungültiger Dateiname"}
            else:
                dst = _safe_path(pre_dir, suggested_path.as_posix())
            if not dst:
                return {"ok": False, "error": "Ungültiger Dateiname"}
            if not dst.parent.exists():
                return {"ok": False, "error": "Zielordner existiert nicht"}
            if dst.exists():
                return {"ok": False, "error": "Datei mit diesem Namen existiert bereits"}
            src.rename(dst)
            if metadata_cache is not None:
                import time as _time

                cached_meta = dict(cached_meta)
                cached_meta["processed"] = True
                cached_meta["processed_at"] = int(_time.time())
                metadata_cache.set(dst, cached_meta)
                metadata_cache.invalidate(src)
            duration_seconds = int(cached_meta.get("duration_seconds") or 0)
            relative_dst_name = _pre_check_relative_name(pre_dir, dst)

            # Rule learning: persist the confirmed oshash → new_name mapping
            rule_learned = False
            if oshash:
                try:
                    from namer_helper.rules import learn_rule
                    rule_learned = learn_rule(
                        helper_config_dir / "rules.yaml",
                        oshash=oshash,
                        suggested_name=relative_dst_name,
                        tpdb_id=tpdb_id or None,
                        source="user_confirmed",
                    )
                except Exception:
                    pass

            return {
                "ok": True,
                "new_name": relative_dst_name,
                "name_encoded": quote(relative_dst_name, safe=""),
                "duration_seconds": duration_seconds,
                "rule_learned": rule_learned,
                "duration_hms": _format_duration(duration_seconds),
                "duration_cached": bool(duration_seconds),
                "processed": True,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/pre-check/send")
    async def pre_check_send(name: str):
        # → watch/ (Namer verarbeitet). Verb: confirm — lernt zusätzlich eine
        # Rule, falls die Datei in der Queue einen Zielnamen hat.
        try:
            ok, error = _act_confirm(name)
            return {"ok": ok, "error": error}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/pre-check/send-all")
    async def pre_check_send_all():
        try:
            pre_dir = _get_pre_check_dir()
            count, skipped = 0, 0
            for video in pre_dir.rglob("*"):
                if not video.is_file():
                    continue
                if video.suffix.lower() not in _VIDEO_EXTS:
                    continue
                if _is_ignored_file(video):
                    continue
                rel = str(video.relative_to(pre_dir))
                ok, _ = _act_confirm(rel)
                if ok:
                    count += 1
                else:
                    skipped += 1
            return {"ok": True, "count": count, "skipped": skipped}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/pre-check/move")
    async def pre_check_move(name: str):
        """Aussortieren: pre-check/ → aussortiert/ (nie löschen). Verb: set_aside."""
        try:
            ok, result = _act_set_aside(name, source_dir=_get_pre_check_dir())
            if not ok:
                return {"ok": False, "error": result}
            return {"ok": True, "moved_to": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Review queue (MVP7) ───────────────────────────────────────────────────

    def _queue_path() -> Path:
        return helper_config_dir / "review-queue.json"

    # ── Unified file-action verbs ─────────────────────────────────────────────
    # One source of truth for the three decisions a file can receive. Every
    # Pre-Check and Queue route calls these — no duplicated move logic, identical
    # side-effects everywhere. set_status() is a no-op when the file is not in
    # the queue, so the same verbs work for plain Pre-Check files too.

    def _act_confirm(
        name: str,
        *,
        suggested_name: str | None = None,
        oshash: str | None = None,
        tpdb_id: str | None = None,
    ) -> tuple[bool, str | None]:
        """Bestätigen: pre-check/ → watch/ (Namer benennt um), Rule lernen, Status.

        Lernt eine Rule nur wenn ein Zielname bekannt ist (aus der Queue oder
        übergeben). Ohne Zielname = reines Verschieben nach watch/.
        """
        from namer_helper import queue as review_queue
        pre_dir = _get_pre_check_dir()
        src = _safe_path(pre_dir, name)
        if not src or not src.exists():
            review_queue.set_status(_queue_path(), name, review_queue.REJECTED)
            return False, "Datei nicht gefunden"

        item = review_queue.get_item(_queue_path(), name)
        if item is not None:
            suggested_name = suggested_name or item.suggested_name
            oshash = oshash or item.oshash
            tpdb_id = tpdb_id or item.tpdb_id

        if not oshash:
            try:
                from namer_helper.namer_bridge.hasher import compute_oshash
                oshash = compute_oshash(src) or ""
            except Exception:
                oshash = ""

        if oshash and suggested_name:
            try:
                from namer_helper.rules import learn_rule
                learn_rule(
                    helper_config_dir / "rules.yaml",
                    oshash=oshash,
                    suggested_name=suggested_name,
                    tpdb_id=tpdb_id or None,
                    source="user_confirmed",
                )
            except Exception:
                pass

        ok, error = _move_to_directory(src, read_namer_paths(namer_config)["watch_dir"])
        if not ok:
            return False, error or "Datei konnte nicht verschoben werden"
        review_queue.set_status(_queue_path(), name, review_queue.CONFIRMED)
        return True, None

    def _act_set_aside(
        name: str, *, source_dir: Path, move_sidecar: bool = False
    ) -> tuple[bool, str | None]:
        """Aussortieren: source_dir → aussortiert/ (nie löschen), Status setzen."""
        from namer_helper import queue as review_queue
        src = _safe_path(source_dir, name)
        if not src or not src.exists():
            return False, "Datei nicht gefunden"
        target = _aussortiert_dir_for(source_dir.parent)
        ok, error = _move_to_directory(src, target)
        if not ok:
            return False, error or "Datei konnte nicht verschoben werden"
        if move_sidecar:
            sidecar = _safe_path(source_dir, f"{Path(name).stem}_namer.json.gz")
            if sidecar and sidecar.exists():
                _move_to_directory(sidecar, target)
        review_queue.set_status(_queue_path(), name, review_queue.REJECTED)
        return True, str(target / src.name)

    def _act_defer(name: str) -> bool:
        """Zurückstellen: Datei bleibt, nur Queue-Status ändern."""
        from namer_helper import queue as review_queue
        return review_queue.set_status(_queue_path(), name, review_queue.DEFERRED)

    @app.get("/queue", response_class=HTMLResponse)
    async def queue_page(request: Request, page: int = 1, per_page: int = _DEFAULT_PER_PAGE):
        from namer_helper import queue as review_queue
        items = review_queue.load_queue(_queue_path())
        ordered = review_queue.sort_for_review(items)
        needs_review_all = [i for i in ordered if i.status == review_queue.PENDING and not review_queue.is_batch_eligible(i)]
        deferred_all = [i for i in ordered if i.status == review_queue.DEFERRED]
        # Paginate the (potentially huge) review list
        pg = _paginate(needs_review_all, page, per_page)
        needs_review = [i.to_dict() for i in pg["slice"]]
        deferred = [i.to_dict() for i in deferred_all[:_MAX_PER_PAGE]]
        # Batch-eligible split by deterministic source — each list capped for render
        # (the batch-confirm buttons act on the FULL queue server-side, not these rows)
        by_cat = review_queue.batch_eligible_by_category(items)
        eligible_groups = {
            cat: [i.to_dict() for i in sorted(by_cat[cat], key=lambda x: -x.confidence)[:_MAX_PER_PAGE]]
            for cat in review_queue.ELIGIBLE_CATEGORIES
        }
        return templates.TemplateResponse(request, "queue.html", {
            "summary": review_queue.summary(items),
            "needs_review": needs_review,
            "needs_review_total": len(needs_review_all),
            "pg": pg,
            "page_url": "/queue",
            "eligible_groups": eligible_groups,
            "deferred": deferred,
        })

    @app.get("/pre-check/queue")
    async def pre_check_queue_list():
        from namer_helper import queue as review_queue
        items = review_queue.load_queue(_queue_path())
        return {
            "ok": True,
            "summary": review_queue.summary(items),
            "items": [i.to_dict() for i in review_queue.sort_for_review(items)],
        }

    @app.post("/pre-check/queue/confirm")
    async def pre_check_queue_confirm(name: str):
        ok, error = _act_confirm(name)
        return {"ok": ok, "error": error}

    @app.post("/pre-check/queue/confirm-batch")
    async def pre_check_queue_confirm_batch(source: str = ""):
        """Confirm batch-eligible items. Optional source = fingerprint|jav|rule
        confirms only that category (sonst alle deterministischen)."""
        from namer_helper import queue as review_queue
        items = review_queue.load_queue(_queue_path())
        eligible = review_queue.batch_eligible(items)
        if source in review_queue.ELIGIBLE_CATEGORIES:
            eligible = [i for i in eligible if review_queue.eligible_category(i) == source]
        confirmed, failed = 0, []
        for item in eligible:
            ok, error = _act_confirm(item.name)
            if ok:
                confirmed += 1
            else:
                failed.append({"name": item.name, "error": error})
        return {"ok": True, "confirmed": confirmed, "failed": failed,
                "eligible": len(eligible)}

    @app.post("/pre-check/queue/reject")
    async def pre_check_queue_reject(name: str):
        # "Aussortieren" — verschiebt die Datei jetzt wirklich nach aussortiert/
        # (vorher nur Status). Selbe Logik wie /pre-check/move.
        ok, error = _act_set_aside(name, source_dir=_get_pre_check_dir())
        return {"ok": ok, "error": error}

    @app.post("/pre-check/queue/defer")
    async def pre_check_queue_defer(name: str):
        return {"ok": _act_defer(name)}

    @app.post("/pre-check/queue/clear-resolved")
    async def pre_check_queue_clear_resolved():
        from namer_helper import queue as review_queue
        removed = review_queue.remove_resolved(_queue_path())
        return {"ok": True, "removed": removed}

    @app.post("/pre-check/queue/reset")
    async def pre_check_queue_reset():
        """Queue komplett leeren (alle Einträge) — für einen frischen Scan.
        Verschiebt/löscht KEINE Dateien, nur die Entscheidungs-Einträge."""
        from namer_helper import queue as review_queue
        removed = review_queue.clear_all(_queue_path())
        return {"ok": True, "removed": removed}

    # ── AI lookup for failed files ────────────────────────────────────────────

    @app.post("/failed/lookup")
    async def failed_lookup(name: str):
        """Query StashDB by oshash and Ollama for filename cleaning. Never raises."""
        try:
            from namer_helper.namer_bridge.hasher import compute_oshash
            from namer_helper.ollama_bridge.analyzer import analyze_filename
            from namer_helper.ollama_bridge.client import OllamaClient
            from namer_helper.stash_bridge.stashdb import StashDBClient
            ai_cfg = load_ai_config(helper_config_dir)

            try:
                paths = read_namer_paths(namer_config)
                failed_dir = paths["failed_dir"]
            except Exception:
                failed_dir = Path("/var/lib/namer/failed")

            # Compute oshash directly from the video file — fast, no extra deps.
            # Namer logs hashes only to journalctl, not to any sidecar file.
            hashes: dict = {"phash": None, "oshash": None, "duration": None}
            video_path = _safe_path(failed_dir, name)
            if video_path and video_path.exists():
                hashes["oshash"] = compute_oshash(video_path)

            # StashDB fingerprint lookup
            stashdb_scenes: list[dict] = []
            stashdb_error: str | None = None
            if hashes["oshash"]:
                client = StashDBClient(api_key=ai_cfg.stashdb_api_key)
                result = client.query_by_fingerprints(oshash=hashes["oshash"])
                stashdb_error = result.error
                stashdb_scenes = [
                    {
                        "title": s.title,
                        "date": s.date,
                        "studio": s.studio,
                        "performers": s.performers,
                        "url": s.stashdb_url,
                    }
                    for s in result.scenes
                ]
            else:
                stashdb_error = "Datei nicht gefunden oder zu klein für Hash"

            # Ollama filename cleaning (always runs if Ollama is reachable)
            ollama_result: dict | None = None
            if ai_cfg.ollama_url:
                ollama = OllamaClient(base_url=ai_cfg.ollama_url)
                if ollama.is_available():
                    res = analyze_filename(name, ollama, model=ai_cfg.ollama_model)
                    ollama_result = {
                        "cleaned_name": res.cleaned_name,
                        "search_queries": res.search_queries,
                        "confidence": res.confidence,
                        "recommended_action": res.recommended_action,
                        "reason": res.reason,
                        "error": res.error,
                    }

            return {
                "ok": True,
                "hashes": hashes,
                "stashdb_scenes": stashdb_scenes,
                "stashdb_error": stashdb_error,
                "ollama": ollama_result,
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "hashes": {"phash": None, "oshash": None, "duration": None},
                "stashdb_scenes": [],
                "stashdb_error": str(exc),
                "ollama": None,
            }

    # ── AI / Integration Settings ─────────────────────────────────────────────

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        ai_cfg = load_ai_config(helper_config_dir)
        ollama_models, ollama_models_error = _list_ollama_models(ai_cfg.ollama_url)
        if ai_cfg.ollama_model and ai_cfg.ollama_model not in ollama_models:
            ollama_models = [ai_cfg.ollama_model, *ollama_models]
        return templates.TemplateResponse(request, "settings.html", {
            "ai_cfg": ai_cfg,
            "ollama_models": ollama_models,
            "ollama_models_error": ollama_models_error,
        })

    @app.get("/settings/ollama-models")
    async def settings_ollama_models(url: str = ""):
        models, error = _list_ollama_models(url.strip())
        return {"ok": error is None, "models": models, "error": error}

    @app.get("/settings/pre-check", response_class=HTMLResponse)
    async def settings_precheck_page(request: Request):
        return templates.TemplateResponse(request, "settings_precheck.html", {
            "ai_cfg": load_ai_config(helper_config_dir),
        })

    @app.post("/settings")
    async def settings_save(request: Request):
        """Merge-Save: nur die im Body vorhandenen Felder werden überschrieben.

        Settings sind über mehrere Tabs verteilt — ein Speichern auf einem Tab
        darf die Felder der anderen Tabs nicht zurücksetzen.
        """
        try:
            body = await request.json()
            cfg = load_ai_config(helper_config_dir)
            if "stashdb_api_key" in body:
                cfg.stashdb_api_key = body["stashdb_api_key"].strip()
            if "theporndb_api_key" in body:
                cfg.theporndb_api_key = body["theporndb_api_key"].strip()
            if "ollama_url" in body:
                cfg.ollama_url = body["ollama_url"].strip() or "http://localhost:11434"
            if "ollama_model" in body:
                cfg.ollama_model = body["ollama_model"].strip() or "llama3"
            if "pre_check_dir" in body:
                cfg.pre_check_dir = body["pre_check_dir"].strip() or "/var/lib/namer/pre-check"
            if "aussortiert_dir" in body:
                cfg.aussortiert_dir = body["aussortiert_dir"].strip()
            save_ai_config(helper_config_dir, cfg)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return app
