"""Tests for the Aussortiert (sorted-out) folder view + restore."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from namer_helper.web.app import create_app


@pytest.fixture
def dirs(tmp_path):
    cfg = tmp_path / "namer.cfg"
    failed = tmp_path / "failed"; failed.mkdir()
    pre = tmp_path / "pre-check"; pre.mkdir()
    aussortiert = tmp_path / "aussortiert"; aussortiert.mkdir()
    cfg.write_text(
        f"[watchdog]\nfailed_dir={failed}\nwatch_dir={tmp_path}/watch\n"
        f"work_dir={tmp_path}/work\ndest_dir={tmp_path}/dest\n"
    )
    reports = tmp_path / "reports"; reports.mkdir()
    config = tmp_path / "config"; config.mkdir()
    (config / "ai_config.json").write_text(json.dumps({
        "pre_check_dir": str(pre), "ollama_url": "", "ollama_model": "llama3",
        "stashdb_api_key": "", "theporndb_api_key": "",
    }))
    return {"cfg": cfg, "reports": reports, "config": config,
            "pre": pre, "failed": failed, "aussortiert": aussortiert}


@pytest.fixture
def client(dirs):
    app = create_app(dirs["cfg"], dirs["reports"], dirs["config"])
    with patch("namer_helper.web.app._check_system_deps"):
        with patch("namer_helper.web.app._is_moondream_available", return_value=False):
            with TestClient(app, raise_server_exceptions=False) as c:
                yield c


class TestSortedOutPage:
    def test_page_renders_empty(self, client):
        r = client.get("/aussortiert")
        assert r.status_code == 200
        assert "Aussortiert" in r.text
        assert "Nichts wird gelöscht" in r.text

    def test_page_lists_files(self, client, dirs):
        (dirs["aussortiert"] / "old_clip.mp4").write_bytes(b"\x00" * 2_000_000)
        r = client.get("/aussortiert")
        assert "old_clip.mp4" in r.text

    def test_page_in_nav_and_dashboard(self, client):
        assert 'href="/aussortiert"' in client.get("/aussortiert").text
        with patch("namer_helper.web.app._service_status", return_value="inactive"):
            assert "aussortiert/" in client.get("/").text


class TestRestore:
    def test_restore_to_precheck(self, client, dirs):
        f = dirs["aussortiert"] / "clip.mp4"; f.write_bytes(b"\x00" * 1000)
        r = client.post("/aussortiert/restore", params={"name": "clip.mp4", "target": "pre-check"})
        assert r.json()["ok"] is True
        assert (dirs["pre"] / "clip.mp4").exists()
        assert not f.exists()

    def test_restore_to_failed(self, client, dirs):
        f = dirs["aussortiert"] / "clip.mp4"; f.write_bytes(b"\x00" * 1000)
        r = client.post("/aussortiert/restore", params={"name": "clip.mp4", "target": "failed"})
        assert r.json()["ok"] is True
        assert (dirs["failed"] / "clip.mp4").exists()

    def test_restore_moves_sidecar_too(self, client, dirs):
        (dirs["aussortiert"] / "clip.mp4").write_bytes(b"\x00" * 1000)
        (dirs["aussortiert"] / "clip_namer.json.gz").write_bytes(b"\x1f\x8b")
        client.post("/aussortiert/restore", params={"name": "clip.mp4", "target": "failed"})
        assert (dirs["failed"] / "clip_namer.json.gz").exists()

    def test_restore_missing_errors(self, client):
        r = client.post("/aussortiert/restore", params={"name": "ghost.mp4"})
        assert r.json()["ok"] is False

    def test_restore_rejects_traversal(self, client):
        r = client.post("/aussortiert/restore", params={"name": "../../etc/passwd"})
        assert r.json()["ok"] is False
