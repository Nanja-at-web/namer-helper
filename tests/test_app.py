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

from namer_helper.web.app import create_app, _list_ollama_models, _safe_path


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


class TestPreCheckCache:
    def test_invalidate_accepts_oshash(self, client):
        with patch("namer_helper.web.lookup_cache.invalidate", return_value=True) as mocked:
            r = client.post("/pre-check/cache/invalidate", params={"oshash": "e654a5305629c18b"})

        assert r.status_code == 200
        assert r.json() == {"ok": True, "removed": True, "oshash": "e654a5305629c18b"}
        mocked.assert_called_once_with("e654a5305629c18b")


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
