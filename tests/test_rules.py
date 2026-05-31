"""Tests for namer_helper/rules/__init__.py — rule learning and lookup."""

import pytest
from pathlib import Path

from namer_helper.rules import (
    Rule,
    PACKAGE_RULES_PATH,
    load_rules,
    learn_rule,
    match_by_hash,
    rule_to_identification,
)


VALID_OSHASH = "7291c281ce4aba3c"
VALID_NAME = "Evil Angel - 2023-05-01 - Scene Title.mp4"


# ── load_rules ────────────────────────────────────────────────────────────────

class TestLoadRules:
    def test_package_rules_file_exists(self):
        assert PACKAGE_RULES_PATH.exists()

    def test_package_rules_empty_by_default(self):
        rules = load_rules()
        assert rules == []

    def test_load_missing_file_returns_empty(self, tmp_path):
        rules = load_rules(tmp_path / "nonexistent.yaml")
        assert rules == []

    def test_load_parses_hash_rule(self, tmp_path):
        p = tmp_path / "rules.yaml"
        p.write_text(
            "version: 1\nrules:\n"
            f"  - type: hash\n    oshash: {VALID_OSHASH}\n"
            f"    suggested_name: {VALID_NAME}\n"
            "    confidence: 1.0\n    source: user_confirmed\n",
            encoding="utf-8",
        )
        rules = load_rules(p)
        assert len(rules) == 1
        assert rules[0].oshash == VALID_OSHASH
        assert rules[0].suggested_name == VALID_NAME
        assert rules[0].confidence == 1.0

    def test_load_skips_entry_without_suggested_name(self, tmp_path):
        p = tmp_path / "rules.yaml"
        p.write_text(
            "version: 1\nrules:\n  - type: hash\n    oshash: abc\n    suggested_name: \"\"\n",
            encoding="utf-8",
        )
        rules = load_rules(p)
        assert rules == []

    def test_load_corrupt_file_returns_empty(self, tmp_path):
        p = tmp_path / "rules.yaml"
        p.write_text("not: valid: yaml: :::", encoding="utf-8")
        rules = load_rules(p)
        assert isinstance(rules, list)

    def test_load_multiple_rules(self, tmp_path):
        p = tmp_path / "rules.yaml"
        p.write_text(
            "version: 1\nrules:\n"
            f"  - type: hash\n    oshash: aaaa0000bbbb1111\n    suggested_name: A.mp4\n"
            f"  - type: hash\n    oshash: cccc2222dddd3333\n    suggested_name: B.mp4\n",
            encoding="utf-8",
        )
        rules = load_rules(p)
        assert len(rules) == 2


# ── match_by_hash ─────────────────────────────────────────────────────────────

class TestMatchByHash:
    def _rules(self, oshash=VALID_OSHASH):
        return [Rule(type="hash", oshash=oshash, suggested_name=VALID_NAME)]

    def test_match_returns_rule(self):
        rules = self._rules()
        assert match_by_hash(VALID_OSHASH, rules) is rules[0]

    def test_no_match_returns_none(self):
        assert match_by_hash("aaaa0000bbbb1111", self._rules()) is None

    def test_empty_oshash_returns_none(self):
        assert match_by_hash("", self._rules()) is None

    def test_invalid_oshash_format_returns_none(self):
        assert match_by_hash("NOTAHASH", self._rules()) is None
        assert match_by_hash("abc", self._rules()) is None

    def test_empty_rules_returns_none(self):
        assert match_by_hash(VALID_OSHASH, []) is None

    def test_only_hash_type_matched(self):
        rule = Rule(type="pattern", oshash=VALID_OSHASH, suggested_name=VALID_NAME)
        assert match_by_hash(VALID_OSHASH, [rule]) is None


# ── learn_rule ────────────────────────────────────────────────────────────────

class TestLearnRule:
    def test_creates_file_with_rule(self, tmp_path):
        p = tmp_path / "rules.yaml"
        result = learn_rule(p, oshash=VALID_OSHASH, suggested_name=VALID_NAME)
        assert result is True
        assert p.exists()
        rules = load_rules(p)
        assert len(rules) == 1
        assert rules[0].oshash == VALID_OSHASH

    def test_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "subdir" / "rules.yaml"
        result = learn_rule(p, oshash=VALID_OSHASH, suggested_name=VALID_NAME)
        assert result is True
        assert p.exists()

    def test_does_not_duplicate_existing_oshash(self, tmp_path):
        p = tmp_path / "rules.yaml"
        learn_rule(p, oshash=VALID_OSHASH, suggested_name=VALID_NAME)
        result = learn_rule(p, oshash=VALID_OSHASH, suggested_name="Other.mp4")
        assert result is False
        rules = load_rules(p)
        assert len(rules) == 1
        assert rules[0].suggested_name == VALID_NAME  # original preserved

    def test_invalid_oshash_returns_false(self, tmp_path):
        p = tmp_path / "rules.yaml"
        assert learn_rule(p, oshash="INVALID", suggested_name=VALID_NAME) is False
        assert learn_rule(p, oshash="", suggested_name=VALID_NAME) is False

    def test_empty_suggested_name_returns_false(self, tmp_path):
        p = tmp_path / "rules.yaml"
        assert learn_rule(p, oshash=VALID_OSHASH, suggested_name="") is False

    def test_tpdb_id_stored(self, tmp_path):
        p = tmp_path / "rules.yaml"
        learn_rule(p, oshash=VALID_OSHASH, suggested_name=VALID_NAME, tpdb_id="scene_123")
        rules = load_rules(p)
        assert rules[0].tpdb_id == "scene_123"

    def test_created_date_is_set(self, tmp_path):
        p = tmp_path / "rules.yaml"
        learn_rule(p, oshash=VALID_OSHASH, suggested_name=VALID_NAME)
        rules = load_rules(p)
        assert rules[0].created is not None

    def test_appends_to_existing_file(self, tmp_path):
        p = tmp_path / "rules.yaml"
        hash2 = "aaaa0000bbbb1111"
        learn_rule(p, oshash=VALID_OSHASH, suggested_name=VALID_NAME)
        learn_rule(p, oshash=hash2, suggested_name="B.mp4")
        rules = load_rules(p)
        assert len(rules) == 2

    def test_custom_source_stored(self, tmp_path):
        p = tmp_path / "rules.yaml"
        learn_rule(p, oshash=VALID_OSHASH, suggested_name=VALID_NAME, source="auto_confirmed")
        rules = load_rules(p)
        assert rules[0].source == "auto_confirmed"


# ── rule_to_identification ────────────────────────────────────────────────────

class TestRuleToIdentification:
    def test_returns_identified_status(self):
        rule = Rule(type="hash", oshash=VALID_OSHASH, suggested_name=VALID_NAME,
                    confidence=1.0, source="user_confirmed")
        ident = rule_to_identification(rule, "original.mp4")
        assert ident["status"] == "identified"
        assert ident["confidence"] == 1.0
        assert ident["action"] == "rename"
        assert ident["suggested_name"] == VALID_NAME

    def test_tpdb_id_in_signals(self):
        rule = Rule(type="hash", oshash=VALID_OSHASH, suggested_name=VALID_NAME,
                    tpdb_id="scene_42")
        ident = rule_to_identification(rule, "original.mp4")
        assert any("scene_42" in s for s in ident["signals"])

    def test_no_tpdb_id_no_signal(self):
        rule = Rule(type="hash", oshash=VALID_OSHASH, suggested_name=VALID_NAME,
                    tpdb_id=None)
        ident = rule_to_identification(rule, "original.mp4")
        assert all("TPDB" not in s for s in ident["signals"])


# ── roundtrip: learn then match ───────────────────────────────────────────────

class TestRoundtrip:
    def test_learn_then_match(self, tmp_path):
        p = tmp_path / "rules.yaml"
        learn_rule(p, oshash=VALID_OSHASH, suggested_name=VALID_NAME)
        rules = load_rules(p)
        matched = match_by_hash(VALID_OSHASH, rules)
        assert matched is not None
        assert matched.suggested_name == VALID_NAME

    def test_different_hash_not_matched(self, tmp_path):
        p = tmp_path / "rules.yaml"
        learn_rule(p, oshash=VALID_OSHASH, suggested_name=VALID_NAME)
        rules = load_rules(p)
        assert match_by_hash("aaaa0000bbbb1111", rules) is None


# ── aliases.py Phase 3 additions ──────────────────────────────────────────────

class TestAliasesPhase3:
    def test_package_performer_aliases_exists(self):
        from namer_helper.aliases import PACKAGE_PERFORMER_ALIASES_PATH
        assert PACKAGE_PERFORMER_ALIASES_PATH.exists()

    def test_performer_aliases_is_empty_by_default(self):
        import json
        from namer_helper.aliases import PACKAGE_PERFORMER_ALIASES_PATH
        data = json.loads(PACKAGE_PERFORMER_ALIASES_PATH.read_text())
        assert data.get("performers") == {}

    def test_merge_combines_studios_and_performers(self):
        from namer_helper.aliases import Aliases, merge
        a = Aliases(studios={"EA": "Evil Angel"}, performers={"JD": "Jane Doe"})
        b = Aliases(studios={"BRZ": "Brazzers"}, performers={"AB": "Alice Brown"})
        c = merge(a, b)
        assert c.studios["EA"] == "Evil Angel"
        assert c.studios["BRZ"] == "Brazzers"
        assert c.performers["JD"] == "Jane Doe"
        assert c.performers["AB"] == "Alice Brown"

    def test_merge_extra_takes_priority(self):
        from namer_helper.aliases import Aliases, merge
        a = Aliases(studios={"EA": "Evil Angel OLD"}, performers={})
        b = Aliases(studios={"EA": "Evil Angel NEW"}, performers={})
        c = merge(a, b)
        assert c.studios["EA"] == "Evil Angel NEW"
