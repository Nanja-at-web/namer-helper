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

    def test_reject_moves_file_and_marks_status(self, client, dirs):
        # Unified verb: reject now MOVES the file to aussortiert/ (bug fix)
        # AND marks the queue item rejected — consistent with /pre-check/move.
        qpath = dirs["config"] / "review-queue.json"
        video = dirs["pre"] / "f.mp4"; video.write_bytes(b"\x00" * 1000)
        q.enqueue(qpath, name="f.mp4", identification=_ident())
        r = client.post("/pre-check/queue/reject", params={"name": "f.mp4"})
        assert r.json()["ok"] is True
        # File physically moved out of pre-check, into aussortiert/
        assert not video.exists()
        assert (dirs["pre"].parent / "aussortiert" / "f.mp4").exists()
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


class TestEligibleCategories:
    """Batch-eligible split by source: fingerprint / jav / rule."""

    def test_categorisation(self):
        def it(src):
            return q.QueueItem(name="f", status=q.PENDING, confidence=0.96,
                               source=src, action="rename")
        assert q.eligible_category(it("StashDB Fingerprint")) == "fingerprint"
        assert q.eligible_category(it("ThePornDB Fingerprint")) == "fingerprint"
        assert q.eligible_category(it("ThePornDB JAV-Code")) == "jav"
        assert q.eligible_category(it("Rule (user_confirmed)")) == "rule"

    def test_group_counts(self, tmp_path):
        p = tmp_path / "q.json"
        q.enqueue(p, name="a.mp4", identification=_ident(source="StashDB Fingerprint", confidence=0.97))
        q.enqueue(p, name="b.mp4", identification=_ident(source="ThePornDB JAV-Code", confidence=0.94))
        q.enqueue(p, name="c.mp4", identification=_ident(source="Rule (x)", confidence=1.0))
        q.enqueue(p, name="d.mp4", identification=_ident(status="likely", confidence=0.76,
                  source="ThePornDB Kontextsuche", action="review"))
        groups = q.batch_eligible_by_category(q.load_queue(p))
        assert len(groups["fingerprint"]) == 1
        assert len(groups["jav"]) == 1
        assert len(groups["rule"]) == 1

    def test_summary_has_category_counts(self, tmp_path):
        p = tmp_path / "q.json"
        q.enqueue(p, name="a.mp4", identification=_ident(source="StashDB Fingerprint", confidence=0.97))
        q.enqueue(p, name="b.mp4", identification=_ident(source="ThePornDB JAV-Code", confidence=0.94))
        s = q.summary(q.load_queue(p))
        assert s["eligible_fingerprint"] == 1
        assert s["eligible_jav"] == 1
        assert s["eligible_rule"] == 0

    def test_confirm_batch_source_filter(self, client, dirs):
        qpath = dirs["config"] / "review-queue.json"
        for n, src in (("fp.mp4", "StashDB Fingerprint"), ("jav.mp4", "ThePornDB JAV-Code")):
            (dirs["pre"] / n).write_bytes(b"\x00" * 1000)
            q.enqueue(qpath, name=n, identification=_ident(source=src, confidence=0.96, suggested=n))
        # Confirm only fingerprint → jav stays
        r = client.post("/pre-check/queue/confirm-batch", params={"source": "fingerprint"}).json()
        assert r["confirmed"] == 1 and r["eligible"] == 1
        assert (dirs["watch"] / "fp.mp4").exists()
        assert (dirs["pre"] / "jav.mp4").exists()       # jav untouched

    def test_confirm_batch_no_source_confirms_all(self, client, dirs):
        qpath = dirs["config"] / "review-queue.json"
        for n, src in (("fp.mp4", "StashDB Fingerprint"), ("jav.mp4", "ThePornDB JAV-Code")):
            (dirs["pre"] / n).write_bytes(b"\x00" * 1000)
            q.enqueue(qpath, name=n, identification=_ident(source=src, confidence=0.96, suggested=n))
        r = client.post("/pre-check/queue/confirm-batch").json()
        assert r["confirmed"] == 2


class TestUnifiedVerbs:
    """Pre-Check and Queue routes share one verb layer — same side-effects."""

    def test_send_learns_rule_when_queued(self, client, dirs):
        # /pre-check/send now uses the confirm verb: if the file is in the queue
        # with a suggested name, sending it also learns a rule.
        qpath = dirs["config"] / "review-queue.json"
        video = dirs["pre"] / "m.mp4"; video.write_bytes(b"\x00" * 1000)
        q.enqueue(qpath, name="m.mp4", identification=_ident(suggested="Clean.mp4"),
                  oshash=VALID_OSHASH)
        r = client.post("/pre-check/send", params={"name": "m.mp4"})
        assert r.json()["ok"] is True
        assert (dirs["watch"] / "m.mp4").exists()
        from namer_helper.rules import load_rules, match_by_hash
        assert match_by_hash(VALID_OSHASH, load_rules(dirs["config"] / "rules.yaml")) is not None

    def test_send_plain_file_not_in_queue_still_moves(self, client, dirs):
        # confirm verb works for a plain pre-check file with no queue entry
        video = dirs["pre"] / "plain.mp4"; video.write_bytes(b"\x00" * 1000)
        r = client.post("/pre-check/send", params={"name": "plain.mp4"})
        assert r.json()["ok"] is True
        assert (dirs["watch"] / "plain.mp4").exists()

    def test_precheck_move_and_queue_reject_same_effect(self, client, dirs):
        # Both verbs land the file in aussortiert/ — identical outcome
        for route, fname in (("/pre-check/move", "a.mp4"), ("/pre-check/queue/reject", "b.mp4")):
            v = dirs["pre"] / fname; v.write_bytes(b"\x00" * 1000)
            if route.endswith("reject"):
                q.enqueue(dirs["config"] / "review-queue.json",
                          name=fname, identification=_ident())
            r = client.post(route, params={"name": fname})
            assert r.json()["ok"] is True
            assert (dirs["pre"].parent / "aussortiert" / fname).exists()


class TestPreCheckMove:
    """Aussortieren must MOVE, never delete (recurring user requirement)."""

    def test_move_relocates_file_not_deletes(self, client, dirs):
        video = dirs["pre"] / "unwanted.mp4"
        video.write_bytes(b"\x00" * 1000)
        r = client.post("/pre-check/move", params={"name": "unwanted.mp4"})
        body = r.json()
        assert body["ok"] is True
        # File is gone from pre-check…
        assert not video.exists()
        # …but preserved in the sort-out folder, never deleted
        sorted_out = dirs["pre"].parent / "aussortiert" / "unwanted.mp4"
        assert sorted_out.exists()

    def test_move_missing_file_errors(self, client):
        r = client.post("/pre-check/move", params={"name": "ghost.mp4"})
        assert r.json()["ok"] is False

    def test_move_rejects_traversal(self, client, dirs):
        r = client.post("/pre-check/move", params={"name": "../../etc/passwd"})
        assert r.json()["ok"] is False


class TestFailedMove:
    """failed/move must relocate video + sidecar, never delete."""

    def test_move_relocates_video_and_sidecar(self, client, dirs, tmp_path):
        failed = tmp_path / "failed"
        failed.mkdir()
        video = failed / "broken.mp4"
        video.write_bytes(b"\x00" * 1000)
        sidecar = failed / "broken_namer.json.gz"
        sidecar.write_bytes(b"\x1f\x8b")  # gzip magic, content irrelevant
        r = client.post("/failed/move", params={"name": "broken.mp4"})
        assert r.json()["ok"] is True
        sorted_out = failed.parent / "aussortiert"
        # Both preserved, neither deleted
        assert (sorted_out / "broken.mp4").exists()
        assert (sorted_out / "broken_namer.json.gz").exists()
        assert not video.exists()
        assert not sidecar.exists()

    def test_move_missing_file_errors(self, client, tmp_path):
        (tmp_path / "failed").mkdir(exist_ok=True)
        r = client.post("/failed/move", params={"name": "ghost.mp4"})
        assert r.json()["ok"] is False

    def test_move_rejects_traversal(self, client, tmp_path):
        (tmp_path / "failed").mkdir(exist_ok=True)
        r = client.post("/failed/move", params={"name": "../../etc/passwd"})
        assert r.json()["ok"] is False


class TestQueuePage:
    """The /queue HTML triage page renders and reflects queue contents."""

    def test_queue_page_renders_empty(self, client):
        r = client.get("/queue")
        assert r.status_code == 200
        assert "Review-Queue" in r.text

    def test_queue_page_shows_review_and_eligible(self, client, dirs):
        qpath = dirs["config"] / "review-queue.json"
        q.enqueue(qpath, name="fp.mp4", identification=_ident(
            confidence=0.96, source="ThePornDB Fingerprint", suggested="FP.mp4"),
            oshash=VALID_OSHASH)
        q.enqueue(qpath, name="ctx.mp4", identification=_ident(
            status="likely", confidence=0.76, source="ThePornDB Kontextsuche",
            action="review", suggested="CTX.mp4"))
        r = client.get("/queue")
        assert r.status_code == 200
        assert "fp.mp4" in r.text
        assert "ctx.mp4" in r.text
        # per-source batch button reflects the one fingerprint-eligible item
        assert "Alle 1 Fingerprint" in r.text

    def test_queue_page_in_nav(self, client):
        r = client.get("/queue")
        assert 'href="/queue"' in r.text


class TestClearAndReset:
    """clear-resolved removes only decided items; reset clears everything."""

    def test_clear_all_module(self, tmp_path):
        p = tmp_path / "q.json"
        for n in ("a", "b", "c"):
            q.enqueue(p, name=f"{n}.mp4", identification=_ident())
        assert q.clear_all(p) == 3
        assert q.load_queue(p) == []

    def test_clear_resolved_keeps_pending(self, client, dirs):
        qp = dirs["config"] / "review-queue.json"
        for n in ("a", "b", "c"):
            q.enqueue(qp, name=f"{n}.mp4", identification=_ident())
        q.set_status(qp, "c.mp4", q.CONFIRMED)
        r = client.post("/pre-check/queue/clear-resolved").json()
        assert r["removed"] == 1                      # only the confirmed one
        names = {i.name for i in q.load_queue(qp)}
        assert names == {"a.mp4", "b.mp4"}            # pending review kept

    def test_reset_clears_everything(self, client, dirs):
        qp = dirs["config"] / "review-queue.json"
        for n in ("a", "b", "c"):
            q.enqueue(qp, name=f"{n}.mp4", identification=_ident())
        r = client.post("/pre-check/queue/reset").json()
        assert r["ok"] is True and r["removed"] == 3
        assert q.load_queue(qp) == []
