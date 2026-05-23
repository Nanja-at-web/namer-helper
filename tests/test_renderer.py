import json
from pathlib import Path
from namer_helper.namer_bridge.log_parser import FailedMatch
from namer_helper.reports.renderer import render_report, render_json, render_markdown


def _match(tmp_path: Path, name: str = "test.mp4", score: float | None = 0.42) -> FailedMatch:
    return FailedMatch(
        file_path=tmp_path / name,
        log_path=tmp_path / f"{name}.namer_failed.log",
        match_score=score,
        site_hint="TestSite",
        date_hint="2024-01-01",
    )


def test_render_json(tmp_path):
    out = tmp_path / "report.json"
    render_json([_match(tmp_path)], out)
    data = json.loads(out.read_text())
    assert data["total"] == 1
    assert data["items"][0]["match_score"] == 0.42
    assert "generated" in data


def test_render_markdown(tmp_path):
    out = tmp_path / "report.md"
    render_markdown([_match(tmp_path)], out)
    content = out.read_text()
    assert "Failed-Match Report" in content
    assert "TestSite" in content
    assert "0.42" in content


def test_render_empty(tmp_path):
    out = tmp_path / "report.md"
    render_markdown([], out)
    assert "Keine fehlgeschlagenen Treffer" in out.read_text()


def test_render_report_both(tmp_path):
    out_dir = tmp_path / "reports"
    written = render_report([_match(tmp_path)], out_dir, fmt="both")
    assert len(written) == 2
    suffixes = {p.suffix for p in written}
    assert suffixes == {".json", ".md"}


def test_render_report_creates_dir(tmp_path):
    out_dir = tmp_path / "nested" / "reports"
    render_report([], out_dir, fmt="json")
    assert out_dir.exists()


def test_anonymize_replaces_filename(tmp_path):
    real_name = "StudioName.2024-01-15.ActressName.mp4"
    m = _match(tmp_path, name=real_name)
    out_dir = tmp_path / "reports"
    written = render_report([m], out_dir, fmt="both", anonymize=True)

    json_path = next(p for p in written if p.suffix == ".json")
    md_path = next(p for p in written if p.suffix == ".md")

    data = json.loads(json_path.read_text())
    assert data["anonymized"] is True
    assert real_name not in data["items"][0]["file"]
    assert "[redacted_" in data["items"][0]["file"]

    md = md_path.read_text()
    assert real_name not in md
    assert "Anonymisiert" in md


def test_anonymize_filename_stable(tmp_path):
    # Same input → same pseudonym across two calls
    from namer_helper.reports.renderer import _pseudonym
    assert _pseudonym("foo.mp4") == _pseudonym("foo.mp4")
    assert _pseudonym("foo.mp4") != _pseudonym("bar.mp4")


def test_anonymize_preserves_extension(tmp_path):
    from namer_helper.reports.renderer import _pseudonym
    assert _pseudonym("scene.mkv").endswith(".mkv")
    assert _pseudonym("scene.mp4").endswith(".mp4")


def test_anonymous_report_filename_contains_marker(tmp_path):
    out_dir = tmp_path / "reports"
    written = render_report([_match(tmp_path)], out_dir, fmt="json", anonymize=True)
    assert "anonymous" in written[0].name
