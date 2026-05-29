from namer_helper.web import metadata_cache


def test_metadata_cache_returns_entry_for_same_file_signature(tmp_path, monkeypatch):
    monkeypatch.setattr(metadata_cache, "CACHE_DIR", tmp_path / "cache")
    video = tmp_path / "example.mp4"
    video.write_bytes(b"example")

    metadata_cache.set(video, {"duration_seconds": 123})

    assert metadata_cache.get(video) == {"duration_seconds": 123}


def test_metadata_cache_invalidates_when_file_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(metadata_cache, "CACHE_DIR", tmp_path / "cache")
    video = tmp_path / "example.mp4"
    video.write_bytes(b"example")
    metadata_cache.set(video, {"duration_seconds": 123})

    video.write_bytes(b"changed content")

    assert metadata_cache.get(video) is None
