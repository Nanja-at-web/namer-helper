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
from namer_helper.web.proxmox import (
    ensure_ssh_key,
    load_proxmox_config,
    run_remote,
    save_proxmox_config,
    setup_host_mount,
    teardown_host_mount,
    ProxmoxConfig,
)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_SERVICE = "namer-watchdog"
_VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".flv"}
_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _is_ignored_file(path: Path) -> bool:
    name = path.name
    if name.startswith((".", "._", "@__")):
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
        ai_cfg = load_ai_config(helper_config_dir)
        pre_dir = Path(ai_cfg.pre_check_dir)
        pre_count = len(_list_pre_check_files(pre_dir)) if pre_dir.exists() else 0
        return templates.TemplateResponse(request, "dashboard.html", {
            "status": _service_status(),
            "stats": _dir_stats(namer_config),
            "reports": _recent_reports(report_output_dir),
            "pre_check_count": pre_count,
            "pre_check_dir": str(pre_dir),
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
    async def failed_list(request: Request):
        try:
            paths = read_namer_paths(namer_config)
            failed_dir = paths["failed_dir"]
        except Exception:
            failed_dir = Path("/var/lib/namer/failed")
        return templates.TemplateResponse(request, "failed.html", {
            "files": _list_failed_files(failed_dir),
            "retry_time": _read_cfg_value(namer_config, "retry_time"),
        })

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

    @app.post("/failed/delete")
    async def failed_delete(name: str):
        try:
            paths = read_namer_paths(namer_config)
            failed_dir = paths["failed_dir"]
            stem = Path(name).stem
            for filename in (name, f"{stem}_namer.json.gz"):
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
            items.append({
                "name": f.name,
                "name_encoded": quote(f.name),
                "size_mb": size_mb,
                "size_bytes": size_bytes,
                "duration_seconds": duration_seconds,
                "duration_hms": _format_duration(duration_seconds),
                "duration_cached": bool(duration_seconds),
                "processed": processed,
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
    async def pre_check_list(request: Request):
        pre_dir = _get_pre_check_dir()
        return templates.TemplateResponse(request, "pre-check.html", {
            "files": _list_pre_check_files(pre_dir),
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
            metadata_cache.set(video_path, {"duration_seconds": seconds})
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
    async def pre_check_lookup(name: str):
        try:
            from namer_helper.namer_bridge.filename_parser import parse_filename
            from namer_helper.namer_bridge.hasher import compute_oshash, compute_phash, detect_studio_logo, extract_frame_text, get_video_info
            from namer_helper.ollama_bridge.analyzer import analyze_filename
            from namer_helper.ollama_bridge.client import OllamaClient
            from namer_helper.stash_bridge.stashdb import StashDBClient
            from namer_helper.stash_bridge.theporndb import ThePornDBClient
            from namer_helper.web import lookup_cache

            loop = asyncio.get_running_loop()
            ai_cfg = load_ai_config(helper_config_dir)
            pre_dir = Path(ai_cfg.pre_check_dir)
            parsed = parse_filename(name)
            ext = Path(name).suffix
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
                        parsed.cleaned or name,
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

            sdb_result, sdb_performer_result, sdb_context_result, tpdb_fp_result, tpdb_movie_fp_result = await asyncio.gather(
                loop.run_in_executor(None, _stashdb),
                loop.run_in_executor(None, _stashdb_performer),
                loop.run_in_executor(None, _stashdb_context),
                loop.run_in_executor(None, _tpdb_fp),
                loop.run_in_executor(None, _tpdb_movie_fp),
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

            if has_primary_result or (tpdb_result and tpdb_result.found) or (tpdb_movie_result and tpdb_movie_result.found) or parsed.performers:
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
                        }
                        for s in tpdb_result.scenes
                    ]
                    if tpdb_result.best:
                        b = tpdb_result.best
                        if b.match_method == "hash" or b.score >= 50:
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
            }
            identification = build_identification(
                original_name=name,
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
            result = {
                "ok": True,
                "hashes": hashes,
                "filename_parsed": filename_parsed,
                "identification": identification,
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
                lookup_cache.set(oshash, result)
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
                if state.get("status") == "stop_requested":
                    scan_status.set_stopped(scan_id)
                    return
                if state.get("status") == "pause_requested":
                    scan_status.set_paused(scan_id)
                    return
                scan_status.mark_running(scan_id, name)
                result = await pre_check_lookup(name)
                scan_status.mark_done(
                    scan_id,
                    name,
                    ok=bool(result.get("ok")),
                    error=result.get("error"),
                    identification=result.get("identification"),
                    result=result,
                )
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
    async def pre_check_cache_invalidate(name: str):
        try:
            from namer_helper.namer_bridge.hasher import compute_oshash
            from namer_helper.web import lookup_cache
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

    @app.post("/pre-check/rename")
    async def pre_check_rename(name: str, new_name: str):
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
            suggested = new_name.strip()
            if Path(suggested).suffix.lower() not in _VIDEO_EXTS:
                suggested = suggested + src.suffix
            dst = _safe_path(pre_dir, suggested)
            if not dst:
                return {"ok": False, "error": "Ungültiger Dateiname"}
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
            return {
                "ok": True,
                "new_name": dst.name,
                "name_encoded": quote(dst.name),
                "duration_seconds": duration_seconds,
                "duration_hms": _format_duration(duration_seconds),
                "duration_cached": bool(duration_seconds),
                "processed": True,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/pre-check/send")
    async def pre_check_send(name: str):
        try:
            pre_dir = _get_pre_check_dir()
            paths = read_namer_paths(namer_config)
            watch_dir = paths["watch_dir"]
            src = _safe_path(pre_dir, name)
            if not src or not src.exists():
                return {"ok": False, "error": "Datei nicht gefunden"}
            ok, error = _move_to_directory(src, watch_dir)
            if not ok:
                return {"ok": False, "error": error or "Datei konnte nicht verschoben werden"}
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/pre-check/send-all")
    async def pre_check_send_all():
        try:
            pre_dir = _get_pre_check_dir()
            paths = read_namer_paths(namer_config)
            watch_dir = paths["watch_dir"]
            count, skipped = 0, 0
            for video in pre_dir.rglob("*"):
                if not video.is_file():
                    continue
                if video.suffix.lower() not in _VIDEO_EXTS:
                    continue
                if _is_ignored_file(video):
                    continue
                try:
                    ok, _ = _move_to_directory(video, watch_dir)
                    if ok:
                        count += 1
                    else:
                        skipped += 1
                except OSError:
                    skipped += 1
            return {"ok": True, "count": count, "skipped": skipped}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/pre-check/delete")
    async def pre_check_delete(name: str):
        try:
            pre_dir = _get_pre_check_dir()
            target = _safe_path(pre_dir, name)
            if target and target.exists():
                target.unlink()
        except Exception:
            pass
        return RedirectResponse("/pre-check", status_code=303)

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
        return templates.TemplateResponse(request, "settings.html", {
            "ai_cfg": ai_cfg,
        })

    @app.post("/settings")
    async def settings_save(request: Request):
        try:
            body = await request.json()
            cfg = AIConfig(
                stashdb_api_key=body.get("stashdb_api_key", "").strip(),
                theporndb_api_key=body.get("theporndb_api_key", "").strip(),
                ollama_url=body.get("ollama_url", "").strip() or "http://localhost:11434",
                ollama_model=body.get("ollama_model", "").strip() or "llama3",
                pre_check_dir=body.get("pre_check_dir", "").strip() or "/var/lib/namer/pre-check",
            )
            save_ai_config(helper_config_dir, cfg)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return app
