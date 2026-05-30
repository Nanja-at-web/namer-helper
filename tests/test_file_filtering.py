from namer_helper.web.app import _is_ignored_file


def test_ignored_file_filters_metadata_and_zero_byte_files(tmp_path):
    normal = tmp_path / "video.mp4"
    normal.write_bytes(b"data")
    apple_double = tmp_path / "._video.mp4"
    apple_double.write_bytes(b"data")
    at_meta = tmp_path / "@__thumb.mp4"
    at_meta.write_bytes(b"data")
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")

    assert _is_ignored_file(normal) is False
    assert _is_ignored_file(apple_double) is True
    assert _is_ignored_file(at_meta) is True
    assert _is_ignored_file(empty) is True
