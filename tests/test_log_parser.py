from pathlib import Path
import pytest
from namer_helper.namer_bridge.log_parser import parse_failed_log, collect_failed_matches


def _write_log(tmp_path: Path, filename: str, content: str) -> Path:
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    return p


def test_parse_score(tmp_path):
    log = _write_log(tmp_path, "some.video.mp4.namer_failed.log", "match score: 0.43\nsite: TestStudio")
    result = parse_failed_log(log)
    assert result.match_score == pytest.approx(0.43)
    assert result.site_hint == "TestStudio"
    assert result.file_path.name == "some.video.mp4"


def test_parse_no_score(tmp_path):
    log = _write_log(tmp_path, "unknown.mp4.namer_failed.log", "no match found")
    result = parse_failed_log(log)
    assert result.match_score is None
    assert result.site_hint is None


def test_parse_date_hint(tmp_path):
    log = _write_log(tmp_path, "file.mp4.namer_failed.log", "date: 2024-03-15\nsite: Studio")
    result = parse_failed_log(log)
    assert result.date_hint == "2024-03-15"


def test_collect_empty_dir(tmp_path):
    results = collect_failed_matches(tmp_path)
    assert results == []


def test_collect_multiple(tmp_path):
    for name in ["a.mp4.namer_failed.log", "b.mp4.namer_failed.log"]:
        _write_log(tmp_path, name, "match score: 0.5")
    results = collect_failed_matches(tmp_path)
    assert len(results) == 2


def test_collect_sorted(tmp_path):
    for name in ["z.mp4.namer_failed.log", "a.mp4.namer_failed.log"]:
        _write_log(tmp_path, name, "")
    results = collect_failed_matches(tmp_path)
    assert results[0].file_path.name == "a.mp4"
    assert results[1].file_path.name == "z.mp4"
