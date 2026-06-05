"""Tests for namer_helper/vocabulary.py + filename_parser integration."""

import json
import pytest
from pathlib import Path

from namer_helper import vocabulary as v
from namer_helper.namer_bridge.filename_parser import parse_filename


# ── learn / load / persistence ────────────────────────────────────────────────

class TestLearnLoad:
    def test_learn_and_load_studios(self, tmp_path):
        n = v.learn(tmp_path, studios=["Evil Angel", "5K Porn"])
        assert n == 2
        vocab = v.load(tmp_path)
        assert vocab.is_studio("Evil Angel")
        assert vocab.is_studio("5K Porn")

    def test_learn_performers(self, tmp_path):
        v.learn(tmp_path, performers=["Alexa Grace", "Blair Summers"])
        vocab = v.load(tmp_path)
        assert vocab.is_performer("Alexa Grace")
        assert not vocab.is_performer("Unknown Person")

    def test_normalized_match_ignores_case_and_punctuation(self, tmp_path):
        v.learn(tmp_path, studios=["Evil Angel"])
        vocab = v.load(tmp_path)
        assert vocab.is_studio("evil angel")
        assert vocab.is_studio("EVIL-ANGEL")
        assert vocab.is_studio("evilangel")

    def test_no_duplicates_counted(self, tmp_path):
        v.learn(tmp_path, studios=["Evil Angel"])
        assert v.learn(tmp_path, studios=["Evil Angel", "evil angel"]) == 0

    def test_short_names_ignored(self, tmp_path):
        assert v.learn(tmp_path, studios=["A", ""]) == 0

    def test_persists_to_json(self, tmp_path):
        v.learn(tmp_path, studios=["Evil Angel"])
        data = json.loads((tmp_path / "known_studios.json").read_text())
        assert "names" in data

    def test_load_missing_returns_empty(self, tmp_path):
        vocab = v.load(tmp_path / "nope")
        assert vocab.studios == {} and vocab.performers == {}

    def test_studio_display_returned(self, tmp_path):
        v.learn(tmp_path, studios=["Evil Angel"])
        vocab = v.load(tmp_path)
        assert vocab.studio_display("EVILANGEL") == "Evil Angel"


# ── filename_parser correction ────────────────────────────────────────────────

class TestParserVocabularyCorrection:
    def test_known_studio_not_kept_as_performer(self, tmp_path):
        v.learn(tmp_path, studios=["Evil Angel", "Tushy"])
        vocab = v.load(tmp_path)
        # Both names are studios → must not end up as performers
        info = parse_filename("Evil Angel & Tushy - Some Title.mp4", vocabulary=vocab)
        assert "Tushy" not in info.performers
        assert "Evil Angel" not in info.performers
        assert vocab.is_studio(info.studio or "")

    def test_without_vocab_unchanged(self):
        # No vocabulary → behaves as before (no correction)
        info = parse_filename("Jane Doe, John Smith - Title.mp4")
        assert "Jane Doe" in info.performers

    def test_real_performer_kept(self, tmp_path):
        v.learn(tmp_path, studios=["Evil Angel"], performers=["Jane Doe"])
        vocab = v.load(tmp_path)
        info = parse_filename("Jane Doe, John Smith - Title.mp4", vocabulary=vocab)
        # Jane Doe is a known performer → stays
        assert "Jane Doe" in info.performers
