"""Tests for namer_helper/queue (MVP7 review queue) + app integration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from namer_helper import queue as q
from namer_helper.web.app import create_app


VALID_OSHASH = "7291c281ce4aba3c"


def _ident(status="identified", confidence=0.96, source="ThePornDB Fingerprint",
           action="rename", suggested="Studio - 2023 - Scene.mp4"):
    return {
        "status": status,
        "confidence": confidence,
        "source": source,
        "action": action,
        "suggested_name": suggested,
        "reason": "test",
    }


# ── is_batch_eligible — the safety boundary ───────────────────────────────────

class TestBatchEligibility:
    def _item(self, **kw):
        base = dict(name="f.mp4", status=q.PENDING, confidence=0.96,
                    source="ThePornDB Fingerprint", action="rename")
        base.update(kw)
        return q.QueueItem(**base)

    def test_fingerprint_is_eligible(self):
        assert q.is_batch_eligible(self._item()) is True

    def test_jav_code_is_eligible(self):
        item = self._item(source="ThePornDB JAV-Code", confidence=0.94)
        assert q.is_batch_eligible(item) is True

    def test_rule_is_eligible(self):
        item = self._item(source="Rule (user_confirmed)", confidence=1.0)
        assert q.is_batch_eligible(item) is True

    def test_context_match_not_eligible(self):
        # identified-from-context: 0.88, source "Kontextsuche" → must stay manual
        item = self._item(source="ThePornDB Kontextsuche", confidence=0.88)
        assert q.is_batch_eligible(item) is False

    def test_high_confidence_context_still_not_eligible(self):
        # Even at 0.95, a non-deterministic source is rejected (defence-in-depth)
        item = self._item(source="StashDB + ThePornDB", confidence=0.95)
        assert q.is_batch_eligible(item) is False

    def test_likely_not_eligible(self):
        item = self._item(status=q.PENDING, confidence=0.76,
                          source="ThePornDB Kontextsuche", action="review")
        assert q.is_batch_eligible(item) is False

    def test_unknown_not_eligible(self):
        item = self._item(confidence=0.0, source="none", action="review")
        assert q.is_batch_eligible(item) is False

    def test_already_confirmed_not_eligible(self):
        item = self._item(status=q.CONFIRMED)
        assert q.is_batch_eligible(item) is False

    def test_duplicate_skip_not_eligible(self):
        # duplicate has confidence 1.0 but action="skip" → not batch-renamed
        item = self._item(source="dest", confidence=1.0, action="skip")
        assert q.is_batch_eligible(item) is False

    def test_exactly_at_threshold_eligible(self):
        item = self._item(source="ThePornDB JAV-Code", confidence=0.94)
        assert q.is_batch_eligible(item) is True

    def test_just_below_threshold_not_eligible(self):
        item = self._item(source="ThePornDB Fingerprint", confidence=0.93)
        assert q.is_batch_eligible(item) is False


# ── enqueue / load / save ─────────────────────────────────────────────────────

class TestEnqueue:
    def test_enqueue_creates_item(self, tmp_path):
        p = tmp_path / "queue.json"
        assert q.enqueue(p, name="f.mp4", identification=_ident(), oshash=VALID_OSHASH)
        items = q.load_queue(p)
        assert len(items) == 1
        assert items[0].name == "f.mp4"
        assert items[0].oshash == VALID_OSHASH

    def test_enqueue_none_identification_skipped(self, tmp_path):
        p = tmp_path / "queue.json"
        assert q.enqueue(p, name="f.mp4", identification=None) is False
        assert q.load_queue(p) == []

    def test_enqueue_updates_pending_item(self, tmp_path):
        p = tmp_path / "queue.json"
        q.enqueue(p, name="f.mp4", identification=_ident(confidence=0.5, source="x"))
        q.enqueue(p, name="f.mp4", identification=_ident(confidence=0.96))
        items = q.load_queue(p)
        assert len(items) == 1
        assert items[0].confidence == pytest.approx(0.96)

    def test_enqueue_does_not_reopen_confirmed(self, tmp_path):
        p = tmp_path / "queue.json"
        q.enqueue(p, name="f.mp4", identification=_ident())
        q.set_status(p, "f.mp4", q.CONFIRMED)
        # Re-scan tries to enqueue again → must be ignored
        result = q.enqueue(p, name="f.mp4", identification=_ident(confidence=0.5))
        assert result is False
        assert q.get_item(p, "f.mp4").status == q.CONFIRMED

    def test_created_at_preserved_on_update(self, tmp_path):
        p = tmp_path / "queue.json"
        q.enqueue(p, name="f.mp4", identification=_ident())
        created = q.get_item(p, "f.mp4").created_at
        q.enqueue(p, name="f.mp4", identification=_ident(confidence=0.5))
        assert q.get_item(p, "f.mp4").created_at == created

    def test_load_missing_file_returns_empty(self, tmp_path):
        assert q.load_queue(tmp_path / "nope.json") == []

    def test_load_corrupt_returns_empty(self, tmp_path):
        p = tmp_path / "queue.json"
        p.write_text("{{ not json")
        assert q.load_queue(p) == []


# ── set_status ────────────────────────────────────────────────────────────────

class TestSetStatus:
    def test_set_valid_status(self, tmp_path):
        p = tmp_path / "queue.json"
        q.enqueue(p, name="f.mp4", identification=_ident())
        assert q.set_status(p, "f.mp4", q.DEFERRED)
        assert q.get_item(p, "f.mp4").status == q.DEFERRED

    def test_set_unknown_item_returns_false(self, tmp_path):
        p = tmp_path / "queue.json"
        assert q.set_status(p, "ghost.mp4", q.CONFIRMED) is False

    def test_invalid_status_rejected(self, tmp_path):
        p = tmp_path / "queue.json"
        q.enqueue(p, name="f.mp4", identification=_ident())
        assert q.set_status(p, "f.mp4", "garbage") is False


# ── sort / summary / batch / remove ───────────────────────────────────────────

class TestSortAndSummary:
    def _populate(self, p):
        q.enqueue(p, name="fp.mp4", identification=_ident(
            confidence=0.96, source="ThePornDB Fingerprint"))
        q.enqueue(p, name="ctx.mp4", identification=_ident(
            status="likely", confidence=0.76, source="ThePornDB Kontextsuche", action="review"))
        q.enqueue(p, name="unk.mp4", identification=_ident(
            status="unknown", confidence=0.0, source="none", action="review"))

    def test_sort_puts_review_first(self, tmp_path):
        p = tmp_path / "queue.json"
        self._populate(p)
        ordered = q.sort_for_review(q.load_queue(p))
        # First group: pending items needing review (not batch-eligible)
        assert ordered[0].name in ("ctx.mp4", "unk.mp4")
        # Batch-eligible fingerprint comes after the manual-review ones
        names = [i.name for i in ordered]
        assert names.index("fp.mp4") > names.index("ctx.mp4")

    def test_summary_counts(self, tmp_path):
        p = tmp_path / "queue.json"
        self._populate(p)
        s = q.summary(q.load_queue(p))
        assert s["total"] == 3
        assert s["batch_eligible"] == 1
        assert s["needs_review"] == 2

    def test_batch_eligible_filters(self, tmp_path):
        p = tmp_path / "queue.json"
        self._populate(p)
        elig = q.batch_eligible(q.load_queue(p))
        assert [i.name for i in elig] == ["fp.mp4"]

    def test_remove_resolved(self, tmp_path):
        p = tmp_path / "queue.json"
        self._populate(p)
        q.set_status(p, "fp.mp4", q.CONFIRMED)
        removed = q.remove_resolved(p)
        assert removed == 1
        assert len(q.load_queue(p)) == 2


# ── app route integration ─────────────────────────────────────────────────────

@pytest.fixture
def dirs(tmp_path):
    cfg = tmp_path / "namer.cfg"
    watch = tmp_path / "watch"
    watch.mkdir()
    pre = tmp_path / "pre-check"
    pre.mkdir()
    cfg.write_text(
        f"[watchdog]\nfailed_dir={tmp_path}/failed\n"
        f"watch_dir={watch}\nwork_dir={tmp_path}/work\ndest_dir={tmp_path}/dest\n"
    )
    reports = tmp_path / "reports"
    reports.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    # ai_config points pre_check_dir at our pre dir
    (config / "ai_config.json").write_text(json.dumps({
        "pre_check_dir": str(pre), "ollama_url": "", "ollama_model": "llama3",
        "stashdb_api_key": "", "theporndb_api_key": "",
    }))
    return {"cfg": cfg, "reports": reports, "config": config, "watch": watch, "pre": pre}


@pytest.fixture
def client(dirs):
    app = create_app(dirs["cfg"], dirs["reports"], dirs["config"])
    with patch("namer_helper.web.app._check_system_deps"):
        with patch("namer_helper.web.app._is_moondream_available", return_value=False):
            with TestClient(app, raise_server_exceptions=False) as c:
                yield c


class TestQueueRoutes:
    def test_queue_list_empty(self, client):
        r = client.get("/pre-check/queue")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["summary"]["total"] == 0

    def test_queue_list_reflects_enqueued(self, client, dirs):
        qpath = dirs["config"] / "review-queue.json"
        q.enqueue(qpath, name="f.mp4", identification=_ident(), oshash=VALID_OSHASH)
        body = client.get("/pre-check/queue").json()
        assert body["summary"]["total"] == 1
        assert body["items"][0]["batch_eligible"] is True

    def test_confirm_moves_file_to_watch(self, client, dirs):
        qpath = dirs["config"] / "review-queue.json"
        video = dirs["pre"] / "movie.mp4"
        video.write_bytes(b"\x00" * 1000)
        q.enqueue(qpath, name="movie.mp4", identification=_ident(suggested="Clean.mp4"),
                  oshash=VALID_OSHASH)
        r = client.post("/pre-check/queue/confirm", params={"name": "movie.mp4"})
        assert r.json()["ok"] is True
        assert not video.exists()                     # moved out of pre-check
        assert (dirs["watch"] / "movie.mp4").exists() # arrived in watch
        assert q.get_item(qpath, "movie.mp4").status == q.CONFIRMED

    def test_confirm_learns_rule(self, client, dirs):
        qpath = dirs["config"] / "review-queue.json"
        video = dirs["pre"] / "movie.mp4"
        video.write_bytes(b"\x00" * 1000)
        q.enqueue(qpath, name="movie.mp4", identification=_ident(suggested="Clean.mp4"),
                  oshash=VALID_OSHASH)
        client.post("/pre-check/queue/confirm", params={"name": "movie.mp4"})
        from namer_helper.rules import load_rules, match_by_hash
        rules = load_rules(dirs["config"] / "rules.yaml")
        assert match_by_hash(VALID_OSHASH, rules) is not None

    def test_confirm_batch_only_eligible(self, client, dirs):
        qpath = dirs["config"] / "review-queue.json"
        # One deterministic (fingerprint), one context (must stay)
        good = dirs["pre"] / "fp.mp4"; good.write_bytes(b"\x00" * 1000)
        ctx = dirs["pre"] / "ctx.mp4"; ctx.write_bytes(b"\x00" * 1000)
        q.enqueue(qpath, name="fp.mp4", identification=_ident(
            confidence=0.96, source="ThePornDB Fingerprint", suggested="FP.mp4"),
            oshash=VALID_OSHASH)
        q.enqueue(qpath, name="ctx.mp4", identification=_ident(
            status="likely", confidence=0.76, source="ThePornDB Kontextsuche",
            action="review", suggested="CTX.mp4"))
        r = client.post("/pre-check/queue/confirm-batch")
        body = r.json()
        assert body["confirmed"] == 1
        assert body["eligible"] == 1
        assert (dirs["watch"] / "fp.mp4").exists()     # eligible moved
        assert ctx.exists()                            # context stayed
        assert (dirs["watch"] / "ctx.mp4").exists() is False

    def test_reject_marks_status(self, client, dirs):
        qpath = dirs["config"] / "review-queue.json"
        q.enqueue(qpath, name="f.mp4", identification=_ident())
        r = client.post("/pre-check/queue/reject", params={"name": "f.mp4"})
        assert r.json()["ok"] is True
        assert q.get_item(qpath, "f.mp4").status == q.REJECTED

    def test_defer_marks_status(self, client, dirs):
        qpath = dirs["config"] / "review-queue.json"
        q.enqueue(qpath, name="f.mp4", identification=_ident())
        client.post("/pre-check/queue/defer", params={"name": "f.mp4"})
        assert q.get_item(qpath, "f.mp4").status == q.DEFERRED

    def test_confirm_missing_file_rejects(self, client, dirs):
        qpath = dirs["config"] / "review-queue.json"
        # Item in queue but file absent from pre-check dir
        q.enqueue(qpath, name="ghost.mp4", identification=_ident(), oshash=VALID_OSHASH)
        r = client.post("/pre-check/queue/confirm", params={"name": "ghost.mp4"})
        assert r.json()["ok"] is False
        assert q.get_item(qpath, "ghost.mp4").status == q.REJECTED

    def test_clear_resolved_route(self, client, dirs):
        qpath = dirs["config"] / "review-queue.json"
        q.enqueue(qpath, name="f.mp4", identification=_ident())
        q.set_status(qpath, "f.mp4", q.CONFIRMED)
        r = client.post("/pre-check/queue/clear-resolved")
        assert r.json()["removed"] == 1
