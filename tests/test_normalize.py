"""Tests for src/namer_helper/normalize.py"""

import pytest
from namer_helper.normalize import normalize, _leet_token, LEET_MAP


# ── _leet_token unit tests ────────────────────────────────────────────────────

class TestLeetToken:
    def test_interior_alpha_replaced(self):
        # 0 between r (alpha) and n (alpha) → at least one alpha → replace
        # pr0n = p+r+o+n = "pron"  (not "porn"; p0rn → porn)
        assert _leet_token("pr0n") == ("pron", True)

    def test_interior_one_digit_neighbour_not_replaced(self):
        # 2 in x264: left x (alpha), right 6 (digit) → interior, not both alpha
        assert _leet_token("x264")[0] == "x264"

    def test_boundary_start_replaced_when_right_alpha(self):
        # 3 at start, right neighbour v is alpha → boundary → replace
        tok, detected = _leet_token("3vil")
        assert tok[0] == "e"
        assert detected is True

    def test_boundary_end_replaced_when_left_alpha(self):
        # 3 at end, left neighbour l is alpha → boundary → replace
        tok, detected = _leet_token("vil3")
        assert tok[-1] == "e"
        assert detected is True

    def test_full_leet_word(self):
        assert _leet_token("3v1l4ng3l") == ("evilangel", True)

    def test_protected_resolution_1080p(self):
        assert _leet_token("1080p") == ("1080p", False)

    def test_protected_4k(self):
        assert _leet_token("4K") == ("4K", False)
        assert _leet_token("4k") == ("4k", False)

    def test_protected_codec_x264(self):
        assert _leet_token("x264") == ("x264", False)

    def test_protected_pure_number(self):
        # Year — all digits, no adjacent alpha
        assert _leet_token("2023") == ("2023", False)

    def test_no_leet_in_plain_word(self):
        assert _leet_token("brazzers") == ("brazzers", False)

    def test_digit_surrounded_by_digits_not_replaced(self):
        # 0 in 108: left 1 digit, right 8 digit → interior, neither alpha
        assert _leet_token("1080")[0] == "1080"

    def test_single_digit_token_no_neighbours(self):
        # Single char "3" has no neighbours → no replacement
        assert _leet_token("3") == ("3", False)

    def test_leet_chain_propagates(self):
        # D03: '0' anchored by 'D' (alpha left) → replaced to 'o'.
        # '3' then sees 'o' (alpha, just set) on the left → replaced to 'e'.
        # Left-to-right propagation through updated chars.
        assert _leet_token("D03") == ("Doe", True)

    def test_episode_marker_protected(self):
        assert _leet_token("S01E05") == ("S01E05", False)
        assert _leet_token("s1e1")   == ("s1e1",   False)
        assert _leet_token("E01")    == ("E01",     False)
        assert _leet_token("EP01")   == ("EP01",    False)


# ── normalize() integration tests ─────────────────────────────────────────────

class TestNormalize:
    def test_leet_word_with_separator(self):
        r = normalize("3v1l.4ng3l.mp4")
        assert r.normalized == "evil.angel"
        assert r.leet_detected is True
        assert r.ext == ".mp4"

    def test_leet_without_separator(self):
        r = normalize("3v1l4ng3l.mp4")
        assert r.normalized == "evilangel"
        assert r.leet_detected is True

    def test_leet_mixed_with_studio(self):
        r = normalize("Studio.3v1l.4ng3l.2023.1080p.mp4")
        assert "evil" in r.normalized
        assert "angel" in r.normalized
        assert r.leet_detected is True

    def test_p0rn_via_leet(self):
        # p0rn → p + o + r + n = porn  (0 at interior, both neighbours alpha)
        r = normalize("p0rn.mp4")
        assert r.normalized == "porn"
        assert r.leet_detected is True

    def test_pr0n_via_leet(self):
        # pr0n → pr + o + n = pron  (not porn; pr0n is a different obfuscation)
        r = normalize("pr0n.mp4")
        assert r.normalized == "pron"
        assert r.leet_detected is True

    def test_protected_resolution_1080p(self):
        r = normalize("Movie.Title.1080p.mp4")
        # 1080p is protected — digits in it are NOT replaced
        assert "1080p" in r.normalized
        assert r.leet_detected is False

    def test_protected_year(self):
        r = normalize("Title.2023.mp4")
        assert "2023" in r.normalized
        assert r.leet_detected is False

    def test_noise_hash(self):
        # # between letters should be removed
        r = normalize("cu#t.mp4")
        assert r.normalized == "cut"
        assert r.noise_detected is True

    def test_noise_asterisk(self):
        r = normalize("p_rn.mp4")  # underscore is a separator, stays
        assert "rn" in r.normalized

    def test_noise_star_in_word(self):
        r = normalize("p*rn.mp4")
        # * removed → prn  (LLM still finds it)
        assert r.normalized == "prn"
        assert r.noise_detected is True

    def test_bracket_noise_removed(self):
        # (3) is bracket noise — removed
        r = normalize("Film - Kopie (3).mp4")
        assert "(3)" not in r.normalized
        assert r.noise_detected is True

    def test_bracket_short_content_removed(self):
        r = normalize("Movie [720p].mp4")
        assert "[720p]" not in r.normalized
        assert r.noise_detected is True

    def test_bracket_removal_no_trailing_space(self):
        # "(3)" is replaced by a space internally, but final .strip() and
        # multi-space collapse ensure no leading/trailing whitespace survives.
        r = normalize("Film (3).mp4")
        assert r.normalized == "Film"
        assert not r.normalized.endswith(" ")
        assert not r.normalized.startswith(" ")

    def test_url_decode(self):
        # %C3%A9 = é  → after unicode normalize → e
        r = normalize("%C3%A9ve.mp4")
        assert r.normalized == "eve"
        assert "url_decode" in r.steps

    def test_unicode_normalize(self):
        r = normalize("Ärger.mp4")
        # Ä → A (after NFKD + ascii encode)
        assert "rger" in r.normalized
        assert "Ä" not in r.normalized

    def test_extension_extracted(self):
        for ext in (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".m4v"):
            r = normalize(f"file{ext}")
            assert r.ext == ext

    def test_unknown_extension_no_ext(self):
        r = normalize("file.txt")
        assert r.ext == ""

    def test_no_extension(self):
        r = normalize("bare_filename")
        assert r.ext == ""

    def test_original_preserved(self):
        filename = "3v1l.4ng3l.mp4"
        r = normalize(filename)
        assert r.original == filename

    def test_separators_preserved(self):
        # normalize does NOT convert . _ - to spaces — downstream tools do that
        r = normalize("Evil.Angel.2023.mp4")
        assert "." in r.normalized

    def test_no_modification_plain_title(self):
        r = normalize("Normal Title Scene.mp4")
        assert r.leet_detected is False
        assert r.noise_detected is False

    def test_steps_recorded_for_leet(self):
        r = normalize("pr0n.mp4")
        assert "leet" in r.steps

    def test_steps_recorded_for_noise(self):
        r = normalize("cu#t.mp4")
        assert "noise_chars" in r.steps

    # ── Lücke 1: Performer-Namen mit Leet ──────────────────────────────────────

    def test_performer_name_leet(self):
        # J4n3 → Jane, D03 → Doe (chain propagation: 0 anchored by D, 3 by o)
        r = normalize("J4n3.D03.mp4")
        assert r.normalized == "Jane.Doe"
        assert r.leet_detected is True

    def test_performer_name_leet_underscore(self):
        r = normalize("J4n3_D03.mp4")
        assert r.normalized == "Jane_Doe"

    # ── Lücke 2: JAV-Code-Schutz ───────────────────────────────────────────────

    def test_jav_code_abp(self):
        # ABP: no leet chars; 123: protected by \d{2,}
        r = normalize("ABP-123.mp4")
        assert r.normalized == "ABP-123"
        assert r.leet_detected is False

    def test_jav_code_ssis(self):
        r = normalize("SSIS-456.mp4")
        assert r.normalized == "SSIS-456"
        assert r.leet_detected is False

    def test_jav_code_with_leet_title(self):
        # Code part stays intact, title part gets leet-normalized
        r = normalize("ABP-123.3v1l.4ng3l.mp4")
        assert "ABP-123" in r.normalized
        assert "evil" in r.normalized
        assert r.leet_detected is True

    # ── Lücke 3: Kombiniert (Leet + Noise im selben Namen) ────────────────────

    def test_combined_leet_noise_performer_year(self):
        # 3v1l#4ng3l → noise char # removed → 3v1l4ng3l → evilangel
        # J4n3 → Jane, D03 → Doe, 2023 → protected
        r = normalize("3v1l#4ng3l.J4n3.D03.2023.mp4")
        assert r.leet_detected is True
        assert r.noise_detected is True
        assert "evil" in r.normalized
        assert "Jane" in r.normalized
        assert "Doe" in r.normalized
        assert "2023" in r.normalized

    def test_all_leet_chars_in_map(self):
        # Smoke test every LEET_MAP entry is exercised at boundaries
        for digit, letter in LEET_MAP.items():
            # Construct a token where leet char is at boundary with one alpha neighbour
            tok, detected = _leet_token(f"{digit}a")
            if digit.isdigit() or digit in "@$!+":
                # Only single-digit tokens are skipped; here we have 2 chars total
                # The leet char is at position 0 (boundary), right neighbour 'a' is alpha
                assert detected, f"Expected leet detection for '{digit}' → '{letter}'"
                assert tok[0] == letter, f"Expected '{letter}' for '{digit}' in '{digit}a'"


class TestResolutionShorthandProtected:
    """5K/8K resolution shorthand must survive leet (5 → s would break studios)."""

    def test_5k_not_leeted(self):
        assert _leet_token("5K") == ("5K", False)
        assert _leet_token("8K") == ("8K", False)

    def test_5k_in_studio_name(self):
        # "5K Porn" must stay "5K Porn", not become "sK Porn"
        r = normalize("5K Porn - White Hot")
        assert "5K Porn" in r.normalized
        assert "sK" not in r.normalized
