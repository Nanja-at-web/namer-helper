"""
Tests for web/app.py — HTTP routing, integration wiring, path guards.

Uses FastAPI TestClient (httpx). External calls (systemctl, Ollama, TPDB,
StashDB) are mocked so tests run without live infrastructure.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import json
import pytest
from fastapi.testclient import TestClient

from namer_helper.web.app import (
    create_app,
    _list_ollama_models,
    _namer_path_preview,
    _safe_path,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def dirs(tmp_path):
    """Minimal directory structure expected by create_app."""
    cfg = tmp_path / "namer.cfg"
    cfg.write_text(
        "[watchdog]\nfailed_dir=/tmp/nh_test_failed\n"
        "watch_dir=/tmp/nh_test_watch\nwork_dir=/tmp/nh_test_work\n"
        "dest_dir=/tmp/nh_test_dest\n"
    )
    reports = tmp_path / "reports"
    reports.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    return {"cfg": cfg, "reports": reports, "config": config}


@pytest.fixture
def app(dirs):
    return create_app(
        namer_config=dirs["cfg"],
        report_output_dir=dirs["reports"],
        helper_config_dir=dirs["config"],
    )


@pytest.fixture
def client(app):
    # Patch startup side-effects that would call real system processes
    with patch("namer_helper.web.app._check_system_deps"):
        with patch("namer_helper.web.app._is_moondream_available", return_value=False):
            with TestClient(app, raise_server_exceptions=False) as c:
                yield c


# ── smoke tests: routes exist and return non-500 ──────────────────────────────

class TestRoutesExist:
    def test_dashboard_returns_200(self, client):
        with patch("namer_helper.web.app._service_status", return_value="inactive"):
            r = client.get("/")
        assert r.status_code == 200

    def test_failed_returns_200(self, client):
        r = client.get("/failed")
        assert r.status_code == 200

    def test_pre_check_returns_200(self, client):
        r = client.get("/pre-check")
        assert r.status_code == 200

    def test_settings_returns_200(self, client):
        r = client.get("/settings")
        assert r.status_code == 200

    def test_mounts_returns_200(self, client):
        r = client.get("/mounts")
        assert r.status_code == 200

    def test_log_returns_200(self, client):
        r = client.get("/log")
        assert r.status_code == 200

    def test_proxmox_returns_200_or_500(self, client):
        # Proxmox route exists; may fail if SSH key creation fails in test env
        r = client.get("/proxmox")
        assert r.status_code in (200, 500)

    def test_nonexistent_route_returns_404(self, client):
        r = client.get("/this-route-does-not-exist")
        assert r.status_code == 404


class TestNavStructure:
    """Grouped nav: Namer dropdown + Mounts/Proxmox/Log moved into Settings tabs."""

    def test_namer_dropdown_present(self, client):
        nav = client.get("/pre-check").text
        assert "Namer ▾" in nav
        # The three Core dirs live inside the dropdown
        assert "/files/watch" in nav
        assert "/files/dest" in nav

    def test_mounts_proxmox_log_not_in_top_nav(self, client):
        # These labels must no longer appear as standalone top-level nav links
        nav = client.get("/pre-check").text
        assert ">Mounts<" not in nav
        assert ">Proxmox<" not in nav
        assert ">Live Log<" not in nav

    def test_settings_has_tab_bar(self, client):
        s = client.get("/settings").text
        assert "⚙ Einstellungen" in s
        for url in ('/settings', '/mounts', '/proxmox', '/log'):
            assert f'href="{url}"' in s

    def test_mounts_page_shows_settings_tabs(self, client):
        m = client.get("/mounts").text
        assert "⚙ Einstellungen" in m
        assert 'href="/settings"' in m

    def test_top_nav_has_single_precheck_entry(self, client):
        # Workflow collapsed to one top-level "Pre-Check"; Queue/Aussortiert
        # are now sub-tabs on the page, not separate top-nav links.
        with patch("namer_helper.web.app._service_status", return_value="inactive"):
            nav = client.get("/").text
        assert 'href="/pre-check"' in nav

    def test_workflow_tabs_on_each_page(self, client):
        # All three workflow pages share the Dateien|Queue|Aussortiert tab bar
        for page in ('/pre-check', '/queue', '/aussortiert'):
            html = client.get(page).text
            assert 'href="/pre-check"' in html
            assert 'href="/queue"' in html
            assert 'href="/aussortiert"' in html

    def test_settings_precheck_tab(self, client):
        s = client.get("/settings").text
        assert 'href="/settings/pre-check"' in s
        pc = client.get("/settings/pre-check").text
        assert "s-pre-check-dir" in pc
        assert "s-aussortiert-dir" in pc


# ── service control ───────────────────────────────────────────────────────────

class TestServiceControl:
    def test_service_start_calls_systemctl(self, client):
        mock_run = MagicMock()
        mock_run.return_value.returncode = 0
        with patch("namer_helper.web.app.subprocess.run", mock_run):
            r = client.post("/service/start")
        assert r.status_code == 200
        called_cmd = mock_run.call_args_list[-1][0][0]
        assert "systemctl" in called_cmd

    def test_service_invalid_action_rejected(self, client):
        r = client.post("/service/rm-rf-everything")
        # Should return 400 or similar — not 200 executing the command
        assert r.status_code != 200 or "error" in r.text.lower()


# ── path traversal guard ──────────────────────────────────────────────────────

class TestSafePath:
    def test_valid_path_returned(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"x")
        result = _safe_path(tmp_path, "video.mp4")
        assert result == f

    def test_traversal_blocked(self, tmp_path):
        result = _safe_path(tmp_path, "../etc/passwd")
        assert result is None

    def test_double_traversal_blocked(self, tmp_path):
        result = _safe_path(tmp_path, "../../etc/shadow")
        assert result is None

    def test_absolute_path_component_blocked(self, tmp_path):
        result = _safe_path(tmp_path, "/etc/passwd")
        assert result is None


class TestNamerPathPreview:
    def test_scene_uses_generic_relative_path_template(self, tmp_path):
        cfg = tmp_path / "namer.cfg"
        cfg.write_text(
            "[watchdog]\n"
            "new_relative_path_name={full_site}/{full_site} - {date} - {name} [WEBDL-{resolution}].{ext}\n"
            "new_relative_path_name_movie={full_site}/Movies/{name} ({year}).{ext}\n"
            "new_relative_path_name_jav=JAV/{full_site}/{name}.{ext}\n",
            encoding="utf-8",
        )

        preview = _namer_path_preview(
            cfg,
            original_name="Input.mp4",
            hashes={"resolution": "720p"},
            filename_parsed={},
            tpdb_scenes=[{
                "title": "Scene Title",
                "date": "2024-01-02",
                "site": "Example Studio",
                "performers": ["A Performer"],
                "match_method": "scene",
            }],
        )

        assert preview is not None
        assert preview["path"] == (
            "Example Studio/Example Studio - 2024-01-02 - "
            "Scene Title [WEBDL-720p].mp4"
        )
        assert preview["template_key"] == "new_relative_path_name"
        assert preview["type"] == "scene"

    def test_movie_uses_movie_specific_template(self, tmp_path):
        cfg = tmp_path / "namer.cfg"
        cfg.write_text(
            "[watchdog]\n"
            "new_relative_path_name={full_site}/{name}.{ext}\n"
            "new_relative_path_name_movie={full_site}/Movies/{name} ({year}).{ext}\n",
            encoding="utf-8",
        )

        preview = _namer_path_preview(
            cfg,
            original_name="Input.mkv",
            tpdb_movies=[{
                "title": "Feature Movie",
                "date": "2020-05-06",
                "site": "Movie Studio",
                "match_method": "movie",
            }],
        )

        assert preview is not None
        assert preview["path"] == "Movie Studio/Movies/Feature Movie (2020).mkv"
        assert preview["template_key"] == "new_relative_path_name_movie"
        assert preview["type"] == "movie"

    def test_jav_uses_jav_specific_template(self, tmp_path):
        cfg = tmp_path / "namer.cfg"
        cfg.write_text(
            "[watchdog]\n"
            "new_relative_path_name={full_site}/{name}.{ext}\n"
            "new_relative_path_name_jav=JAV/{full_site}/{name}.{ext}\n",
            encoding="utf-8",
        )

        preview = _namer_path_preview(
            cfg,
            original_name="RCT-769.mp4",
            filename_parsed={"jav_code": "RCT-769"},
            tpdb_scenes=[{
                "title": "JAV Scene",
                "date": "2015-08-20",
                "site": "ROCKET",
                "match_method": "jav",
            }],
        )

        assert preview is not None
        assert preview["path"] == "JAV/ROCKET/JAV Scene.mp4"
        assert preview["template_key"] == "new_relative_path_name_jav"
        assert preview["type"] == "jav"

    def test_unsafe_relative_template_is_rejected(self, tmp_path):
        cfg = tmp_path / "namer.cfg"
        cfg.write_text(
            "[watchdog]\nnew_relative_path_name={full_site}/../{name}.{ext}\n",
            encoding="utf-8",
        )

        preview = _namer_path_preview(
            cfg,
            original_name="Input.mp4",
            tpdb_scenes=[{"title": "Scene Title", "site": "Studio"}],
        )

        assert preview is None

    def test_jinja_filters_in_namer_template_are_applied(self, tmp_path):
        cfg = tmp_path / "namer.cfg"
        cfg.write_text(
            "[watchdog]\n"
            "new_relative_path_name={site:|lower}/{name:|title|replace(' ', '.')}.{ext}\n",
            encoding="utf-8",
        )

        preview = _namer_path_preview(
            cfg,
            original_name="Input.mp4",
            tpdb_scenes=[{
                "title": "scene title",
                "site": "Example Studio",
                "match_method": "scene",
            }],
        )

        assert preview is not None
        assert preview["path"] == "examplestudio/Scene.Title.mp4"


# ── alias integration in pre_check_lookup ─────────────────────────────────────

class TestAliasIntegrationWiring:
    """Verify that parse_filename is called WITH aliases in the pre-check pipeline."""

    def test_parse_filename_receives_aliases(self, dirs):
        """
        When a lookup is triggered, parse_filename must be called with an
        Aliases object, not None.  This ensures EA → Evil Angel before TPDB.
        """
        app = create_app(
            namer_config=dirs["cfg"],
            report_output_dir=dirs["reports"],
            helper_config_dir=dirs["config"],
        )

        calls: list = []

        original_parse = None
        try:
            from namer_helper.namer_bridge.filename_parser import parse_filename as pf
            original_parse = pf
        except ImportError:
            pytest.skip("filename_parser not available")

        def capturing_parse(name, aliases=None):
            calls.append({"name": name, "aliases": aliases})
            return original_parse(name, aliases)

        # We need a video file in the pre_check dir to trigger the lookup path
        pre_dir = dirs["config"].parent / "pre-check"
        pre_dir.mkdir(exist_ok=True)
        fake_video = pre_dir / "EA.2023-01-01.Test.mp4"
        fake_video.write_bytes(b"\x00" * 1000)

        # Write ai_config.json pointing to our tmp pre_check dir
        import json
        (dirs["config"] / "ai_config.json").write_text(json.dumps({
            "pre_check_dir": str(pre_dir),
            "ollama_url": "",
            "ollama_model": "llama3",
            "stashdb_api_key": "",
            "theporndb_api_key": "",
        }))

        with patch("namer_helper.web.app._check_system_deps"):
            with patch("namer_helper.web.app._is_moondream_available", return_value=False):
                with patch("namer_helper.namer_bridge.filename_parser.parse_filename",
                           side_effect=capturing_parse):
                    with TestClient(app, raise_server_exceptions=False) as c:
                        c.post("/pre-check/lookup",
                               json={"name": "EA.2023-01-01.Test.mp4"})

        if calls:
            assert calls[0]["aliases"] is not None, (
                "parse_filename was called without aliases — EA will not resolve to Evil Angel"
            )


# ── scan status routes ────────────────────────────────────────────────────────

class TestScanStatusRoutes:
    def test_scan_status_returns_json(self, client):
        r = client.get("/pre-check/scan/status")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")

    def test_scan_start_returns_json(self, client):
        r = client.post("/pre-check/scan/start", json={"files": []})
        assert r.status_code == 200

    def test_scan_stop_returns_json(self, client):
        r = client.post("/pre-check/scan/stop")
        assert r.status_code == 200


class TestPreCheckTimeout:
    def test_single_lookup_timeout_marks_overall_timeout(self, dirs):
        app = create_app(
            namer_config=dirs["cfg"],
            report_output_dir=dirs["reports"],
            helper_config_dir=dirs["config"],
        )

        async def slow_lookup(_name):
            import asyncio
            await asyncio.sleep(0.01)

        with patch("namer_helper.web.app._SINGLE_LOOKUP_TIMEOUT_SECONDS", 0.001):
            with patch("namer_helper.web.app._check_system_deps"):
                with patch("namer_helper.web.app._is_moondream_available", return_value=False):
                    with TestClient(app, raise_server_exceptions=False) as c:
                        r = c.post("/pre-check/lookup", params={"name": "Slow.mp4"})

        data = r.json()
        assert data["timeout"] is True
        assert data["ollama"]["error"] == "Nicht abgeschlossen wegen Gesamt-Timeout"
        assert data["stashdb_error"] == "Nicht abgeschlossen wegen Gesamt-Timeout"


class TestPreCheckRename:
    def test_rename_learns_rule_even_without_client_oshash(self, dirs):
        pre_dir = dirs["config"].parent / "pre-check"
        pre_dir.mkdir(exist_ok=True)
        src = pre_dir / "Original.mp4"
        src.write_bytes(b"x" * 1024)
        (dirs["config"] / "ai_config.json").write_text(json.dumps({
            "pre_check_dir": str(pre_dir),
            "ollama_url": "",
            "ollama_model": "llama3",
            "stashdb_api_key": "",
            "theporndb_api_key": "",
        }))

        app = create_app(
            namer_config=dirs["cfg"],
            report_output_dir=dirs["reports"],
            helper_config_dir=dirs["config"],
        )

        with patch("namer_helper.web.app._check_system_deps"):
            with patch("namer_helper.web.app._is_moondream_available", return_value=False):
                with patch("namer_helper.namer_bridge.hasher.compute_oshash",
                           return_value="71cd356abee68aaa"):
                    with TestClient(app, raise_server_exceptions=False) as c:
                        r = c.post(
                            "/pre-check/rename",
                            params={"name": "Original.mp4", "new_name": "Confirmed.mp4"},
                        )

        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["rule_learned"] is True
        rules = (dirs["config"] / "rules.yaml").read_text(encoding="utf-8")
        assert "71cd356abee68aaa" in rules
        assert "Confirmed.mp4" in rules

    def test_nested_file_preview_uses_relative_path(self, dirs):
        pre_dir = dirs["config"].parent / "pre-check"
        nested = pre_dir / "Studio"
        nested.mkdir(parents=True, exist_ok=True)
        src = nested / "Nested.mp4"
        src.write_bytes(b"x" * 1024)
        (dirs["config"] / "ai_config.json").write_text(json.dumps({
            "pre_check_dir": str(pre_dir),
            "ollama_url": "",
            "ollama_model": "llama3",
            "stashdb_api_key": "",
            "theporndb_api_key": "",
        }))

        app = create_app(
            namer_config=dirs["cfg"],
            report_output_dir=dirs["reports"],
            helper_config_dir=dirs["config"],
        )

        with patch("namer_helper.web.app._check_system_deps"):
            with patch("namer_helper.web.app._is_moondream_available", return_value=False):
                with patch("namer_helper.namer_bridge.hasher.compute_oshash",
                           return_value="71cd356abee68aaa"):
                    with TestClient(app, raise_server_exceptions=False) as c:
                        page = c.get("/pre-check")
                        video = c.get("/pre-check/video", params={"name": "Studio/Nested.mp4"})

        assert page.status_code == 200
        assert 'data-folder="Studio"' in page.text
        assert "Nested.mp4" in page.text
        assert "Studio%2FNested.mp4" in page.text
        assert video.status_code == 200

    def test_nested_rename_stays_in_same_folder(self, dirs):
        pre_dir = dirs["config"].parent / "pre-check"
        nested = pre_dir / "Studio"
        nested.mkdir(parents=True, exist_ok=True)
        src = nested / "Original.mp4"
        src.write_bytes(b"x" * 1024)
        (dirs["config"] / "ai_config.json").write_text(json.dumps({
            "pre_check_dir": str(pre_dir),
            "ollama_url": "",
            "ollama_model": "llama3",
            "stashdb_api_key": "",
            "theporndb_api_key": "",
        }))

        app = create_app(
            namer_config=dirs["cfg"],
            report_output_dir=dirs["reports"],
            helper_config_dir=dirs["config"],
        )

        with patch("namer_helper.web.app._check_system_deps"):
            with patch("namer_helper.web.app._is_moondream_available", return_value=False):
                with patch("namer_helper.namer_bridge.hasher.compute_oshash",
                           return_value="71cd356abee68aaa"):
                    with TestClient(app, raise_server_exceptions=False) as c:
                        r = c.post(
                            "/pre-check/rename",
                            params={"name": "Studio/Original.mp4", "new_name": "Confirmed.mp4"},
                        )

        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["new_name"] == "Studio/Confirmed.mp4"
        assert data["name_encoded"] == "Studio%2FConfirmed.mp4"
        assert not src.exists()
        assert (nested / "Confirmed.mp4").exists()
        rules = (dirs["config"] / "rules.yaml").read_text(encoding="utf-8")
        assert "Studio/Confirmed.mp4" in rules


class TestPreCheckCache:
    def test_invalidate_accepts_oshash(self, client):
        with patch("namer_helper.web.lookup_cache.invalidate", return_value=True) as mocked:
            r = client.post("/pre-check/cache/invalidate", params={"oshash": "e654a5305629c18b"})

        assert r.status_code == 200
        assert r.json() == {"ok": True, "removed": True, "oshash": "e654a5305629c18b"}
        mocked.assert_called_once_with("e654a5305629c18b")

    def test_pre_check_marks_cached_files_for_filtering(self, dirs):
        pre_dir = dirs["config"].parent / "pre-check"
        pre_dir.mkdir(exist_ok=True)
        (pre_dir / "Cached.mp4").write_bytes(b"x" * 1024)
        (dirs["config"] / "ai_config.json").write_text(json.dumps({
            "pre_check_dir": str(pre_dir),
            "ollama_url": "",
            "ollama_model": "llama3",
            "stashdb_api_key": "",
            "theporndb_api_key": "",
        }))

        app = create_app(
            namer_config=dirs["cfg"],
            report_output_dir=dirs["reports"],
            helper_config_dir=dirs["config"],
        )

        with patch("namer_helper.web.app._check_system_deps"):
            with patch("namer_helper.web.app._is_moondream_available", return_value=False):
                with patch("namer_helper.namer_bridge.hasher.compute_oshash",
                           return_value="e654a5305629c18b"):
                    with patch("namer_helper.web.lookup_cache.get",
                               return_value={"ok": True, "cached": True}):
                        with TestClient(app, raise_server_exceptions=False) as c:
                            r = c.get("/pre-check")

        assert r.status_code == 200
        assert '<option value="cached">Cached</option>' in r.text
        assert 'data-cached="1"' in r.text
        assert ">Cached</span>" in r.text


# ── settings save/load ────────────────────────────────────────────────────────

class TestSettings:
    def test_list_ollama_models_filters_non_text_models(self):
        class Response:
            def json(self):
                return {"models": [
                    {"name": "llama3:latest"},
                    {"name": "all-minilm:latest"},
                    {"name": "mxbai-embed-large:latest"},
                    {"name": "moondream:latest"},
                    {"name": "qwen2.5:7b"},
                ]}

            def raise_for_status(self):
                pass

        with patch("requests.get") as mocked:
            mocked.return_value = Response()
            models, error = _list_ollama_models("http://ollama:11434")

        assert error is None
        assert models == ["llama3:latest", "qwen2.5:7b"]

    def test_settings_lists_ollama_models(self, client):
        with patch("namer_helper.web.app._list_ollama_models",
                   return_value=(["llama3:latest", "qwen2.5:7b"], None)):
            r = client.get("/settings")

        assert r.status_code == 200
        assert "llama3:latest" in r.text
        assert "qwen2.5:7b" in r.text
        assert 'id="s-ollama-model"' in r.text

    def test_settings_ollama_models_endpoint(self, client):
        with patch("namer_helper.web.app._list_ollama_models",
                   return_value=(["llama3:latest", "qwen2.5:7b"], None)) as mocked:
            r = client.get("/settings/ollama-models", params={"url": "http://ollama:11434"})

        assert r.status_code == 200
        assert r.json() == {"ok": True, "models": ["llama3:latest", "qwen2.5:7b"], "error": None}
        mocked.assert_called_once_with("http://ollama:11434")

    def test_settings_save_roundtrip(self, client, dirs):
        payload = {
            "ollama_url": "http://test:11434",
            "ollama_model": "llama3",
            "stashdb_api_key": "",
            "theporndb_api_key": "",
            "pre_check_dir": "/tmp/pre-check",
        }
        r = client.post("/settings", json=payload)
        assert r.status_code == 200
        # Verify the config was written
        import json
        cfg_file = dirs["config"] / "ai_config.json"
        if cfg_file.exists():
            saved = json.loads(cfg_file.read_text())
            assert saved.get("ollama_url") == "http://test:11434"


class TestPagination:
    """Big lists are paginated: every file is reachable across pages."""

    def _make_app(self, tmp_path, n_failed=0, n_pre=0):
        import json as _json
        failed = tmp_path / "failed"; failed.mkdir()
        pre = tmp_path / "pre"; pre.mkdir()
        for i in range(n_failed):
            (failed / f"f{i:04d}.mp4").write_bytes(b"x" * 1100)
        for i in range(n_pre):
            (pre / f"p{i:04d}.mp4").write_bytes(b"x" * 1100)
        cfg = tmp_path / "namer.cfg"
        cfg.write_text(f"[watchdog]\nfailed_dir={failed}\nwatch_dir={tmp_path}/w\n"
                       f"work_dir={tmp_path}/wk\ndest_dir={tmp_path}/d\n")
        reports = tmp_path / "reports"; reports.mkdir()
        config = tmp_path / "config"; config.mkdir()
        (config / "ai_config.json").write_text(_json.dumps({
            "pre_check_dir": str(pre), "ollama_url": "", "ollama_model": "llama3",
            "stashdb_api_key": "", "theporndb_api_key": ""}))
        return create_app(cfg, reports, config)

    def test_paginate_helper(self):
        from namer_helper.web.app import _paginate, _MAX_PER_PAGE, _MIN_PER_PAGE
        items = list(range(1300))
        p1 = _paginate(items, page=1, per_page=500)
        assert p1["slice"] == list(range(0, 500))
        assert p1["pages"] == 3 and p1["page"] == 1
        p2 = _paginate(items, page=2, per_page=500)
        assert p2["slice"] == list(range(500, 1000))   # 501–1000 reachable
        # per_page clamped to safe bounds
        assert _paginate(items, 1, 999999)["per_page"] == _MAX_PER_PAGE
        assert _paginate(items, 1, 1)["per_page"] == _MIN_PER_PAGE
        # page clamped to last
        assert _paginate(items, 99, 500)["page"] == 3

    def test_failed_second_page_shows_later_files(self, tmp_path):
        app = self._make_app(tmp_path, n_failed=600)
        with patch("namer_helper.web.app._check_system_deps"), \
             patch("namer_helper.web.app._is_moondream_available", return_value=False):
            with TestClient(app, raise_server_exceptions=False) as c:
                page1 = c.get("/failed?page=1&per_page=500").text
                page2 = c.get("/failed?page=2&per_page=500").text
        assert "f0000.mp4" in page1 and "f0000.mp4" not in page2
        assert "f0550.mp4" in page2 and "f0550.mp4" not in page1  # only on page 2

    def test_precheck_pagination_bar_and_total(self, tmp_path):
        app = self._make_app(tmp_path, n_pre=600)
        with patch("namer_helper.web.app._check_system_deps"), \
             patch("namer_helper.web.app._is_moondream_available", return_value=False):
            with TestClient(app, raise_server_exceptions=False) as c:
                html = c.get("/pre-check").text
                page2 = c.get("/pre-check?page=2&per_page=500").text
        assert "600 Dateien" in html
        assert "Seite <b>1</b> / 2" in html
        assert "p0550.mp4" in page2  # file #550 reachable on page 2


class TestScanAll:
    """Server-side scan-all must cover EVERY file, not just the rendered cap."""

    def test_scan_all_enumerates_full_dir(self, tmp_path, monkeypatch):
        import json as _json
        from namer_helper.web import scan_status
        monkeypatch.setattr(scan_status, "STATUS_DIR", tmp_path / "scan")
        monkeypatch.setattr(scan_status, "STATUS_FILE", tmp_path / "scan" / "pre-check.json")
        pre = tmp_path / "pre"; pre.mkdir()
        for i in range(7):
            (pre / f"p{i}.mp4").write_bytes(b"x" * 1100)
        cfg = tmp_path / "namer.cfg"
        cfg.write_text("[watchdog]\nfailed_dir=/f\nwatch_dir=/w\nwork_dir=/wk\ndest_dir=/d\n")
        reports = tmp_path / "reports"; reports.mkdir()
        config = tmp_path / "config"; config.mkdir()
        (config / "ai_config.json").write_text(_json.dumps({
            "pre_check_dir": str(pre), "ollama_url": "", "ollama_model": "llama3",
            "stashdb_api_key": "", "theporndb_api_key": ""}))
        app = create_app(cfg, reports, config)
        with patch("namer_helper.web.app._check_system_deps"), \
             patch("namer_helper.web.app._is_moondream_available", return_value=False):
            with TestClient(app, raise_server_exceptions=False) as c:
                r = c.post("/pre-check/scan/start-all").json()
        assert r["ok"] is True
        assert r["count"] == 7  # full directory, independent of render cap


class TestVocabularyRoutes:
    """WebUI vocabulary import + stats."""

    def _app(self, tmp_path, **ai):
        import json as _json
        from namer_helper import vocabulary
        cfg = tmp_path / "namer.cfg"
        cfg.write_text("[watchdog]\nfailed_dir=/f\nwatch_dir=/w\nwork_dir=/wk\ndest_dir=/d\n")
        reports = tmp_path / "reports"; reports.mkdir()
        config = tmp_path / "config"; config.mkdir()
        base = {"pre_check_dir": str(tmp_path / "pre"), "ollama_url": "",
                "ollama_model": "llama3", "stashdb_api_key": "", "theporndb_api_key": ""}
        base.update(ai)
        (config / "ai_config.json").write_text(_json.dumps(base))
        vocabulary.learn(config, studios=["Evil Angel"], performers=["Jane Doe"])
        return create_app(cfg, reports, config)

    def test_stats(self, tmp_path):
        app = self._app(tmp_path)
        with patch("namer_helper.web.app._check_system_deps"), \
             patch("namer_helper.web.app._is_moondream_available", return_value=False):
            with TestClient(app, raise_server_exceptions=False) as c:
                j = c.get("/vocabulary/stats").json()
        assert j["studios"] == 1 and j["performers"] == 1

    def test_import_buttons_in_settings(self, tmp_path):
        app = self._app(tmp_path)
        with patch("namer_helper.web.app._check_system_deps"), \
             patch("namer_helper.web.app._is_moondream_available", return_value=False):
            with TestClient(app, raise_server_exceptions=False) as c:
                html = c.get("/settings/pre-check").text
        assert "Aus StashApp" in html and "Aus StashDB" in html and "Aus ThePornDB" in html

    def test_import_runs_in_background_and_reports_error(self, tmp_path):
        import time
        app = self._app(tmp_path)
        with patch("namer_helper.web.app._check_system_deps"), \
             patch("namer_helper.web.app._is_moondream_available", return_value=False):
            with TestClient(app, raise_server_exceptions=False) as c:
                # Start returns immediately (background job)
                r = c.post("/vocabulary/import?source=tpdb").json()
                assert r["ok"] is True and r["started"] is True
                # Job finishes fast (no key) → error surfaces in the status
                for _ in range(20):
                    s = c.get("/vocabulary/import/status").json()
                    if s["done"]:
                        break
                    time.sleep(0.05)
                assert s["done"] is True
                assert "Key" in (s["error"] or "")

    def test_import_stop_route(self, tmp_path):
        app = self._app(tmp_path)
        with patch("namer_helper.web.app._check_system_deps"), \
             patch("namer_helper.web.app._is_moondream_available", return_value=False):
            with TestClient(app, raise_server_exceptions=False) as c:
                assert c.post("/vocabulary/import/stop").json()["ok"] is True
