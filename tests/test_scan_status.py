from namer_helper.web import scan_status


def test_scan_status_persists_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_status, "STATUS_DIR", tmp_path)
    monkeypatch.setattr(scan_status, "STATUS_FILE", tmp_path / "pre-check.json")

    state = scan_status.start(["one.mp4", "two.mp4"])
    scan_id = state["scan_id"]

    scan_status.mark_running(scan_id, "one.mp4")
    scan_status.mark_done(scan_id, "one.mp4", ok=True, identification={"status": "identified"})

    restored = scan_status.load()
    assert restored["active"] is True
    assert restored["done"] == 1
    assert restored["items"][0]["status"] == "done"
    assert restored["items"][0]["identification"] == {"status": "identified"}

    scan_status.finish(scan_id)
    restored = scan_status.load()
    assert restored["active"] is False
    assert restored["status"] == "finished"
