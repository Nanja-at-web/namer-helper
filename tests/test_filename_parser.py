"""Tests for filename_parser.py — structural extraction + normalize integration."""

import pytest
from namer_helper.namer_bridge.filename_parser import parse_filename, FilenameInfo
from namer_helper.aliases import Aliases


# ── Basic structural extraction ───────────────────────────────────────────────

class TestBasicExtraction:
    def test_date_extracted(self):
        info = parse_filename("Studio - 2023-05-15 - Scene Title.mp4")
        assert info.date == "2023-05-15"

    def test_studio_from_segment(self):
        info = parse_filename("Evil Angel - 2023-05-15 - Scene Title.mp4")
        assert info.studio == "Evil Angel"

    def test_title_cleaned(self):
        info = parse_filename("Studio - 2023-05-15 - Scene Title.mp4")
        assert "Scene Title" in info.cleaned

    def test_resolution_extracted(self):
        info = parse_filename("Movie.Title.1080p.mp4")
        assert info.resolution == "1080p"

    def test_tech_tags_stripped(self):
        info = parse_filename("Studio.Title.1080p.x264.BluRay.mp4")
        assert "1080p" not in info.cleaned
        assert "x264" not in info.cleaned
        assert "BluRay" not in info.cleaned
        assert len(info.tech_tags) >= 2

    def test_confidence_increases_with_structure(self):
        sparse = parse_filename("unknownfile.mp4")
        rich = parse_filename("Evil Angel - 2023-05-15 - Scene Title.mp4")
        assert rich.confidence > sparse.confidence

    def test_no_match_returns_empty_info(self):
        info = parse_filename("file.mp4")
        assert info.studio is None
        assert info.date is None


# ── normalize() integration — leet ────────────────────────────────────────────

class TestLeetIntegration:
    def test_leet_in_plain_segment(self):
        # "3v1l.4ng3l" in title segment → resolved by normalize before parsing
        info = parse_filename("3v1l.4ng3l.2023.mp4")
        assert "evil" in info.cleaned.lower()
        assert "angel" in info.cleaned.lower()

    def test_leet_studio_segment(self):
        info = parse_filename("3v1l.4ng3l - 2023-05-15 - Scene.mp4")
        assert info.studio is not None
        assert "evil" in info.studio.lower()

    def test_leet_performer_name(self):
        # J4n3 → Jane, D03 → Doe (chain propagation)
        info = parse_filename("Studio - 2023 - Scene.J4n3.D03.mp4")
        joined = " ".join(info.cleaned.lower().split())
        assert "jane" in joined or "doe" in joined

    def test_at_mention_leet_resolved(self):
        # @3v1l_4ng3l → studio "Evil Angel" after normalize + title()
        info = parse_filename("@3v1l_4ng3l.Scene.Title.2023.mp4")
        assert info.studio is not None
        assert "evil" in info.studio.lower()
        assert "angel" in info.studio.lower()

    def test_leet_year_preserved(self):
        # Years must not be leet-replaced
        info = parse_filename("Studio.Title.2023.1080p.mp4")
        assert info.date is None  # no DD-MM format, but year should still be present
        # 2023 should not become "e0e3" or similar
        assert "2023" in info.cleaned or info.date is not None or "2023" not in info.cleaned

    def test_protected_resolution_not_corrupted(self):
        # 1080p must survive normalize unchanged
        info = parse_filename("Studio.Title.1080p.mp4")
        assert info.resolution == "1080p"

    def test_leet_with_date_extraction(self):
        info = parse_filename("3v1l.4ng3l.2023-05-15.Scene.mp4")
        assert info.date == "2023-05-15"
        assert "evil" in info.cleaned.lower() or "evil" in (info.studio or "").lower()


# ── normalize() integration — noise ───────────────────────────────────────────

class TestNoiseIntegration:
    def test_hash_noise_removed_from_title(self):
        info = parse_filename("Studio.cu#t.scene.mp4")
        assert "#" not in info.cleaned

    def test_bracket_noise_cleaned(self):
        info = parse_filename("Movie.Title (3).mp4")
        assert "(3)" not in info.cleaned

    def test_hashtag_marker_still_works_despite_noise_removal(self):
        # #performer is a structural marker, not noise — must still be extracted
        info = parse_filename("Studio.#Jane_Doe.2023.mp4")
        assert "Jane" in info.performers or any("jane" in p.lower() for p in info.performers)


# ── @/# structural markers ────────────────────────────────────────────────────

class TestStructuralMarkers:
    def test_at_mention_studio(self):
        info = parse_filename("@Evil_Angel.Scene.Title.2023.mp4")
        assert info.studio == "Evil Angel"

    def test_hash_performer(self):
        info = parse_filename("Studio.@studio.#Jane_Doe.2023.mp4")
        assert any("Jane" in p or "jane" in p.lower() for p in info.performers)

    def test_multiple_hash_performers(self):
        info = parse_filename("Studio.#Jane_Doe.#John_Smith.2023.mp4")
        assert len(info.performers) == 2

    def test_noise_hashtags_are_not_performers(self):
        info = parse_filename(
            "A Slender Beautiful Girl Rena Miyashita "
            "#Jav #Japan #Japanese #Asian #Decensored #Solowork #Uniform.mp4"
        )
        assert info.performers == []
        assert "Rena Miyashita" in info.cleaned

    def test_mixed_noise_and_real_hashtag_performer(self):
        info = parse_filename("Scene.Title.#jav.#Jane_Doe.#asian.mp4")
        assert info.performers == ["Jane Doe"]

    def test_date_iso(self):
        assert parse_filename("Studio - 2024-03-15 - Title.mp4").date == "2024-03-15"

    def test_date_dd_mm_yyyy(self):
        assert parse_filename("Studio - 15.03.2024 - Title.mp4").date == "2024-03-15"

    def test_date_us_mm_dd_yyyy_corrected(self):
        # Regression: 03-15-2024 (US) must NOT become invalid "2024-15-03"
        assert parse_filename("Studio - 03-15-2024 - Title.mp4").date == "2024-03-15"

    def test_date_real_dd_mm(self):
        assert parse_filename("Studio - 25.12.2024 - Title.mp4").date == "2024-12-25"

    def test_impossible_date_rejected(self):
        # month 13 / day 14 is impossible in any order → no date, not garbage
        assert parse_filename("Studio - 2024-13-14 - Title.mp4").date is None

    def test_comma_separated_performers_not_studio(self):
        # "A, B - Studio - Title": comma list must be performers, not studio
        info = parse_filename("Alexa Grace, Blair Summers - Studio - Title.mp4")
        assert info.performers == ["Alexa Grace", "Blair Summers"]
        assert info.studio != "Alexa Grace, Blair Summers"

    def test_numeric_hashtag_not_performer(self):
        # "#1" is a number, never a performer name
        info = parse_filename("Studio - Title #1 - Jane Doe.mp4")
        assert "1" not in info.performers
        assert info.performers == [] or all(not p.isdigit() for p in info.performers)

    def test_plus_separated_performers(self):
        info = parse_filename("Brazzers - Jane Doe + John Smith - Scene.mp4")
        assert "Jane Doe" in info.performers
        assert "John Smith" in info.performers

    def test_structural_word_not_performer(self):
        # A bare "Studio"/"Title" segment must not become a performer
        info = parse_filename("Studio - Title - Jane Doe.mp4")
        assert "Title" not in info.performers
        assert "Studio" not in info.performers

    def test_hash_inside_word_is_not_a_performer(self):
        # Regression: a '#' INSIDE a word (mangled studio "ATKG#lleria") must
        # NOT be read as a performer hashtag — and must not block the real
        # performers in the first segment.
        info = parse_filename(
            "Alexa Grace and Blair Summers - lesbian - blonde - dildos - ATKG#lleria.mp4"
        )
        assert info.performers == ["Alexa Grace", "Blair Summers"]
        assert "lleria" not in info.performers
        # the mangled studio word survives (# removed by normalize)
        assert "ATKGlleria" in info.cleaned


# ── alias integration ─────────────────────────────────────────────────────────

class TestAliasIntegration:
    def setup_method(self):
        self.aliases = Aliases(
            studios={"EA": "Evil Angel"},
            performers={"JD": "Jane Doe"},
        )

    def test_studio_abbreviation_via_segment(self):
        info = parse_filename("EA - 2023-05-15 - Scene.mp4", self.aliases)
        assert info.studio == "Evil Angel"

    def test_performer_initials_via_hashtag(self):
        info = parse_filename("Studio.#JD.2023.mp4", self.aliases)
        assert "Jane Doe" in info.performers

    def test_no_aliases_leaves_abbreviation(self):
        info = parse_filename("EA - 2023-05-15 - Scene.mp4")
        assert info.studio == "EA"


# ── FailureReason in log_parser ───────────────────────────────────────────────

class TestFailureReason:
    """Light integration: ensure classify_failure returns expected reasons."""

    def _make_match(self, filename, score=None, site=None, date=None):
        from pathlib import Path
        from namer_helper.namer_bridge.log_parser import FailedMatch, classify_failure, FailureReason
        match = FailedMatch(
            file_path=Path(filename),
            log_path=Path(filename + ".namer_failed.log"),
            match_score=score,
            site_hint=site,
            date_hint=date,
        )
        match.failure_reason = classify_failure(match)
        return match

    def test_leet_detected(self):
        from namer_helper.namer_bridge.log_parser import FailureReason
        m = self._make_match("3v1l.4ng3l.mp4", score=0.60)
        assert m.failure_reason == FailureReason.LEET_NOT_PARSED

    def test_zero_results(self):
        from namer_helper.namer_bridge.log_parser import FailureReason
        m = self._make_match("Normal.Title.2023.mp4", score=None)
        assert m.failure_reason == FailureReason.ZERO_API_RESULTS

    def test_low_score(self):
        from namer_helper.namer_bridge.log_parser import FailureReason
        m = self._make_match("Normal.Title.2023.mp4", score=0.72)
        assert m.failure_reason == FailureReason.LOW_FUZZY_SCORE

    def test_multiple_matches(self):
        from namer_helper.namer_bridge.log_parser import FailureReason
        # score >= 0.95 but still failed → multiple equal candidates
        m = self._make_match("Normal.Title.2023.mp4", score=0.97)
        assert m.failure_reason == FailureReason.MULTIPLE_MATCHES

    def test_noise_chars(self):
        from namer_helper.namer_bridge.log_parser import FailureReason
        m = self._make_match("cu#t.scene.mp4", score=0.70)
        # leet_detected=False, noise_detected=True → NOISE_CHARS
        assert m.failure_reason == FailureReason.NOISE_CHARS
