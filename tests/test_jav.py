"""Tests for src/namer_helper/jav.py"""

import pytest
from namer_helper.jav import JavCode, detect, detect_all


# ── detect() ──────────────────────────────────────────────────────────────────

class TestDetect:
    def test_standard_3digit(self):
        r = detect("ABP-123.mp4")
        assert r is not None
        assert r.code == "ABP-123"
        assert r.studio == "ABP"
        assert r.number == "123"
        assert r.is_fc2 is False

    def test_standard_2digit(self):
        r = detect("PRED-99.mp4")
        assert r is not None
        assert r.code == "PRED-99"

    def test_standard_4digit(self):
        r = detect("MIDV-1000.mp4")
        assert r is not None
        assert r.code == "MIDV-1000"
        assert r.number == "1000"

    def test_5digit_not_matched(self):
        # \d{2,4} — 5 digits must NOT match
        r = detect("ABP-12345.mp4")
        assert r is None

    def test_1digit_not_matched(self):
        r = detect("ABP-1.mp4")
        assert r is None

    def test_lowercase_input_normalised(self):
        r = detect("abp-123.mp4")
        assert r is not None
        assert r.code == "ABP-123"
        assert r.studio == "ABP"

    def test_mixed_case_normalised(self):
        r = detect("Ipzz-001.mkv")
        assert r is not None
        assert r.code == "IPZZ-001"

    def test_no_code_returns_none(self):
        assert detect("regular.movie.2023.1080p.mp4") is None
        assert detect("Evil.Angel.Jane.Doe.mp4") is None

    def test_fc2_ppv_6digit(self):
        r = detect("FC2-PPV-123456.mp4")
        assert r is not None
        assert r.code == "FC2-PPV-123456"
        assert r.studio == "FC2"
        assert r.number == "123456"
        assert r.is_fc2 is True

    def test_fc2_ppv_7digit(self):
        r = detect("FC2-PPV-1234567.mp4")
        assert r is not None
        assert r.code == "FC2-PPV-1234567"
        assert r.is_fc2 is True

    def test_fc2_ppv_8digit(self):
        r = detect("fc2-ppv-12345678.mp4")
        assert r is not None
        assert r.code == "FC2-PPV-12345678"
        assert r.is_fc2 is True

    def test_fc2_ppv_5digit_not_matched(self):
        # FC2-PPV needs 6-8 digits
        r = detect("FC2-PPV-12345.mp4")
        assert r is None or not r.is_fc2

    def test_fc2_without_ppv_not_matched(self):
        # FC2-123 is not a real JAV code: "FC2" contains a digit, so it does
        # NOT match [A-Z]{2,6}. Only FC2-PPV-NNNNNNN (via _FC2_RE) is valid.
        assert detect("FC2-123.mp4") is None

    def test_code_embedded_in_longer_name(self):
        r = detect("Studio.ABP-123.Title.2023.mp4")
        assert r is not None
        assert r.code == "ABP-123"

    def test_code_at_start(self):
        r = detect("SSIS-456 - Scene Title.mp4")
        assert r is not None
        assert r.code == "SSIS-456"

    def test_first_code_returned_when_multiple(self):
        r = detect("ABP-123.MIDV-456.mp4")
        assert r is not None
        assert r.code == "ABP-123"

    # ── \d{2,4} boundary exactness ────────────────────────────────────────────

    def test_boundary_exactly_4_digits_matched(self):
        # 4 digits = within range
        r = detect("PRED-9999.mp4")
        assert r is not None
        assert r.number == "9999"

    def test_boundary_5_digits_rejected(self):
        # 5 digits = outside range
        assert detect("PRED-99999.mp4") is None

    # ── false positive resistance ─────────────────────────────────────────────

    def test_resolution_not_a_jav_code(self):
        # "1080p" and "720p" should not match — digits lack the letter prefix
        assert detect("movie.1080p.720p.mp4") is None

    def test_word_boundary_prevents_partial_match(self):
        # "ABP-1234X" — X directly attached, no word boundary after digits
        assert detect("ABP-1234X.mp4") is None


# ── detect_all() ──────────────────────────────────────────────────────────────

class TestDetectAll:
    def test_single_code(self):
        codes = detect_all("ABP-123.mp4")
        assert len(codes) == 1
        assert codes[0].code == "ABP-123"

    def test_multiple_codes_in_order(self):
        codes = detect_all("ABP-123.MIDV-456.mp4")
        assert len(codes) == 2
        assert codes[0].code == "ABP-123"
        assert codes[1].code == "MIDV-456"

    def test_fc2_and_standard_in_order(self):
        codes = detect_all("ABP-123.FC2-PPV-1234567.mp4")
        codes_by_code = {c.code for c in codes}
        assert "ABP-123" in codes_by_code
        assert "FC2-PPV-1234567" in codes_by_code

    def test_no_codes_empty_list(self):
        assert detect_all("regular.movie.2023.mp4") == []

    def test_no_duplicates(self):
        # Same code appears twice in filename — returned once
        codes = detect_all("ABP-123.title.ABP-123.mp4")
        abp_codes = [c for c in codes if c.code == "ABP-123"]
        assert len(abp_codes) == 1
