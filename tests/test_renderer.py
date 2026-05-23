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
