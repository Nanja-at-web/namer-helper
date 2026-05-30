from namer_helper.web import scan_status


def test_scan_status_persists_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_status, "STATUS_DIR", tmp_path)
    monkeypatch.setattr(scan_status, "STATUS_FILE", tmp_path / "pre-check.json")

    state = scan_status.start(["one.mp4", "two.mp4"])
    scan_id = state["scan_id"]

    scan_status.mark_running(scan_id, "one.mp4")
    scan_status.mark_done(
        scan_id,
        "one.mp4",
        ok=True,
        identification={"status": "identified"},
        result={"ok": True, "identification": {"status": "identified"}},
    )

    restored = scan_status.load()
    assert restored["active"] is True
    assert restored["done"] == 1
    assert restored["items"][0]["status"] == "done"
    assert restored["items"][0]["identification"] == {"status": "identified"}
    assert restored["items"][0]["result"] == {"ok": True, "identification": {"status": "identified"}}

    scan_status.finish(scan_id)
    restored = scan_status.load()
    assert restored["active"] is False
    assert restored["status"] == "finished"


def test_scan_status_pause_resume_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_status, "STATUS_DIR", tmp_path)
    monkeypatch.setattr(scan_status, "STATUS_FILE", tmp_path / "pre-check.json")

    state = scan_status.start(["one.mp4"])
    scan_id = state["scan_id"]

    paused = scan_status.pause(scan_id)
    assert paused["status"] == "pause_requested"

    scan_status.set_paused(scan_id)
    assert scan_status.load()["status"] == "paused"

    resumed = scan_status.resume()
    assert resumed["active"] is True
    assert resumed["status"] == "running"

    stopped = scan_status.stop(scan_id)
    assert stopped["active"] is False
    assert stopped["status"] == "stopped"
    final = scan_status.load()
    assert final["active"] is False
    assert final["status"] == "stopped"

    state = scan_status.start(["two.mp4"])
    scan_status.set_paused(state["scan_id"])
    final = scan_status.stop(state["scan_id"])
    assert final["active"] is False
    assert final["status"] == "stopped"
