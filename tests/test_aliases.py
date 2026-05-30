"""Tests for src/namer_helper/aliases.py and filename_parser integration."""

import json
import pytest
from pathlib import Path

from namer_helper.aliases import Aliases, load, resolve_studio, resolve_performer, learn
from namer_helper.namer_bridge.filename_parser import parse_filename


# ── load() ────────────────────────────────────────────────────────────────────

class TestLoad:
    def test_load_missing_file_returns_defaults(self, tmp_path):
        aliases = load(tmp_path / "nonexistent.json")
        assert "EA" in aliases.studios
        assert aliases.studios["EA"] == "Evil Angel"

    def test_load_reads_studios(self, tmp_path):
        p = tmp_path / "aliases.json"
        p.write_text(json.dumps({"studios": {"FOO": "FooStudio"}}))
        aliases = load(p)
        assert aliases.studios["FOO"] == "FooStudio"

    def test_load_normalises_keys_to_uppercase(self, tmp_path):
        p = tmp_path / "aliases.json"
        p.write_text(json.dumps({"studios": {"ea": "Evil Angel"}}))
        aliases = load(p)
        # Key is uppercased on load
        assert "EA" in aliases.studios
        assert "ea" not in aliases.studios

    def test_load_reads_performers(self, tmp_path):
        p = tmp_path / "aliases.json"
        p.write_text(json.dumps({"performers": {"JD": "Jane Doe"}}))
        aliases = load(p)
        assert aliases.performers["JD"] == "Jane Doe"

    def test_load_corrupt_file_returns_defaults(self, tmp_path):
        p = tmp_path / "aliases.json"
        p.write_text("not json {{{")
        aliases = load(p)
        assert "EA" in aliases.studios


# ── resolve_studio() ──────────────────────────────────────────────────────────

class TestResolveStudio:
    def setup_method(self):
        self.aliases = Aliases(
            studios={"EA": "Evil Angel", "BRZ": "Brazzers"},
            performers={"JD": "Jane Doe"},
        )

    def test_known_abbreviation(self):
        assert resolve_studio("EA", self.aliases) == "Evil Angel"

    def test_case_insensitive_lookup(self):
        assert resolve_studio("ea", self.aliases) == "Evil Angel"
        assert resolve_studio("Ea", self.aliases) == "Evil Angel"

    def test_unknown_returns_original(self):
        assert resolve_studio("UNKNOWN", self.aliases) == "UNKNOWN"

    def test_full_name_unchanged(self):
        # Full name not in studios table → returned unchanged
        assert resolve_studio("Evil Angel", self.aliases) == "Evil Angel"

    def test_all_default_studios_resolve(self):
        from namer_helper.aliases import _DEFAULT_STUDIOS
        aliases = Aliases(studios=dict(_DEFAULT_STUDIOS))
        for abbr, full in _DEFAULT_STUDIOS.items():
            assert resolve_studio(abbr, aliases) == full


# ── resolve_performer() ───────────────────────────────────────────────────────

class TestResolvePerformer:
    def setup_method(self):
        self.aliases = Aliases(
            studios={},
            performers={"JD": "Jane Doe", "AB": "Alice Brown"},
        )

    def test_known_performer(self):
        assert resolve_performer("JD", self.aliases) == "Jane Doe"

    def test_case_insensitive(self):
        assert resolve_performer("jd", self.aliases) == "Jane Doe"

    def test_unknown_returns_original(self):
        assert resolve_performer("Unknown", self.aliases) == "Unknown"


# ── learn() ───────────────────────────────────────────────────────────────────

class TestLearn:
    def test_learn_new_studio(self, tmp_path):
        p = tmp_path / "aliases.json"
        learn(p, "studios", "NEW", "New Studio")
        data = json.loads(p.read_text())
        assert data["studios"]["NEW"] == "New Studio"

    def test_learn_creates_file_if_missing(self, tmp_path):
        p = tmp_path / "subdir" / "aliases.json"
        learn(p, "studios", "XX", "XX Studio")
        assert p.exists()

    def test_learn_does_not_overwrite_existing(self, tmp_path):
        p = tmp_path / "aliases.json"
        p.write_text(json.dumps({"studios": {"EA": "Evil Angel"}, "performers": {}}))
        learn(p, "studios", "EA", "Something Else")
        data = json.loads(p.read_text())
        assert data["studios"]["EA"] == "Evil Angel"  # unchanged

    def test_learn_performer(self, tmp_path):
        p = tmp_path / "aliases.json"
        learn(p, "performers", "jd", "Jane Doe")
        data = json.loads(p.read_text())
        # Key stored uppercase
        assert data["performers"]["JD"] == "Jane Doe"

    def test_learn_empty_key_ignored(self, tmp_path):
        p = tmp_path / "aliases.json"
        learn(p, "studios", "", "No Key")
        assert not p.exists()

    def test_learn_empty_value_ignored(self, tmp_path):
        p = tmp_path / "aliases.json"
        learn(p, "studios", "XX", "")
        assert not p.exists()


# ── filename_parser integration ───────────────────────────────────────────────

class TestFilenameParserIntegration:
    def setup_method(self):
        self.aliases = Aliases(
            studios={"EA": "Evil Angel", "BRZ": "Brazzers"},
            performers={"JD": "Jane Doe"},
        )

    def test_studio_abbreviation_resolved(self):
        info = parse_filename("EA - 2023-05-01 - Some Scene.mp4", self.aliases)
        assert info.studio == "Evil Angel"

    def test_studio_full_name_unchanged(self):
        info = parse_filename("Evil Angel - 2023-05-01 - Some Scene.mp4", self.aliases)
        assert info.studio == "Evil Angel"

    def test_no_aliases_leaves_abbreviation(self):
        info = parse_filename("EA - 2023-05-01 - Some Scene.mp4")
        assert info.studio == "EA"  # not resolved without aliases

    def test_performer_abbreviation_resolved(self):
        info = parse_filename("Studio @studio #JD.2023.mp4", self.aliases)
        # #JD hashtag → performer "JD" → resolved to "Jane Doe"
        assert "Jane Doe" in info.performers

    def test_unknown_studio_unchanged(self):
        info = parse_filename("UNKNOWNST - 2023-05-01 - Scene.mp4", self.aliases)
        assert info.studio == "UNKNOWNST"
