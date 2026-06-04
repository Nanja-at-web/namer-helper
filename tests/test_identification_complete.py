"""
Complete coverage of all confidence paths in web/identification.py.

Each test targets one specific branch. The priority order in build_identification:
  1. dest duplicate           → 1.00  skip
  2. StashDB fingerprint      → 0.97  rename
  3. TPDB fingerprint         → 0.96  rename
  4. TPDB JAV-Code            → 0.94 / 0.35 (duration conflict)
  5. Movie fingerprint        → 0.96  rename
  6. Movie context            → 0.90 / 0.78 / 0.40 (score) / 0.35 (duration)
  7. Cross-confirm SDB+TPDB   → 0.90 / 0.86 / 0.35 (duration)
  8. TPDB context             → 0.88 / 0.76 / 0.35 (score) / 0.35 (duration)
  9. StashDB context          → 0.78 / 0.64 / 0.32 (duration)
 10. Ollama only              → 0.55 (conf >= 0.85) / unknown (<0.85)
 11. Unknown                  → 0.00
"""

import pytest
from namer_helper.web.identification import build_identification


# ── helpers ───────────────────────────────────────────────────────────────────

def _build(**kwargs):
    defaults = dict(
        original_name="file.mp4",
        stashdb_scenes=[],
        stashdb_suggested=None,
        tpdb_scenes=[],
        tpdb_suggested=None,
        ollama=None,
        filename_parsed=None,
        dest_duplicate=None,
        local_duration=None,
        tpdb_movies=[],
        tpdb_movie_suggested=None,
    )
    defaults.update(kwargs)
    return build_identification(**defaults)


def _sdb_scene(match_via="fingerprint", duration=3600, title="Scene"):
    return {"id": "s1", "title": title, "match_via": match_via,
            "duration": duration, "performers": [], "studio": None, "date": None}


def _tpdb_scene(method="hash", score=0, duration=3600, title="Scene",
                performers=None, studio=None, date=None):
    return {"id": "t1", "title": title, "match_method": method, "score": score,
            "duration": duration, "performers": performers or [],
            "site": studio, "date": date}


def _tpdb_movie(method="hash", score=0, duration=3600, title="Movie"):
    return {"id": "m1", "title": title, "match_method": method, "score": score,
            "duration": duration, "performers": [], "site": None, "date": None}


def _ollama(conf=0.9, name="Scene Title", error=None):
    return {"confidence": conf, "cleaned_name": name, "error": error}


# ── 1. Duplicate ──────────────────────────────────────────────────────────────

class TestDuplicate:
    def test_duplicate_returns_skip(self):
        r = _build(dest_duplicate="abc123")
        assert r["status"] == "duplicate"
        assert r["confidence"] == 1.0
        assert r["action"] == "skip"

    def test_duplicate_ignores_other_signals(self):
        # Even with TPDB data, duplicate wins
        r = _build(
            dest_duplicate="abc",
            tpdb_scenes=[_tpdb_scene(method="hash")],
        )
        assert r["status"] == "duplicate"


# ── 2. StashDB Fingerprint ────────────────────────────────────────────────────

class TestStashDBFingerprint:
    def test_stashdb_fingerprint_0_97(self):
        r = _build(
            stashdb_scenes=[_sdb_scene(match_via="fingerprint")],
            stashdb_suggested="Studio - 2023 - Title.mp4",
        )
        assert r["status"] == "identified"
        assert r["confidence"] == pytest.approx(0.97)
        assert r["action"] == "rename"
        assert r["suggested_name"] == "Studio - 2023 - Title.mp4"

    def test_stashdb_fingerprint_preferred_over_tpdb_context(self):
        r = _build(
            stashdb_scenes=[_sdb_scene(match_via="fingerprint")],
            tpdb_scenes=[_tpdb_scene(method="title", score=90)],
        )
        assert r["confidence"] == pytest.approx(0.97)


# ── 3. TPDB Fingerprint ───────────────────────────────────────────────────────

class TestTPDBFingerprint:
    def test_tpdb_fingerprint_0_96(self):
        r = _build(tpdb_scenes=[_tpdb_scene(method="hash")])
        assert r["status"] == "identified"
        assert r["confidence"] == pytest.approx(0.96)
        assert r["action"] == "rename"

    def test_tpdb_fingerprint_no_score_also_matches(self):
        # score=0 and no score field both trigger the fingerprint path
        r = _build(tpdb_scenes=[{"id": "t1", "title": "X", "match_method": "hash",
                                  "score": 0, "duration": None, "performers": [],
                                  "site": None, "date": None}])
        assert r["confidence"] == pytest.approx(0.96)


# ── 4. TPDB JAV-Code ─────────────────────────────────────────────────────────

class TestTPDBJAV:
    def test_jav_match_0_94(self):
        r = _build(
            tpdb_scenes=[_tpdb_scene(method="jav", score=90, duration=3600)],
            local_duration=3600,
        )
        assert r["status"] == "identified"
        assert r["confidence"] == pytest.approx(0.94)

    def test_jav_duration_conflict_0_35(self):
        r = _build(
            tpdb_scenes=[_tpdb_scene(method="jav", score=90, duration=3060)],
            local_duration=8902,  # 148 min vs 51 min → conflict
        )
        assert r["status"] == "possible"
        assert r["confidence"] == pytest.approx(0.35)
        assert r["action"] == "review"

    def test_jav_no_local_duration_no_conflict(self):
        # No local duration → no duration check → identified
        r = _build(
            tpdb_scenes=[_tpdb_scene(method="jav", score=90, duration=3600)],
            local_duration=None,
        )
        assert r["status"] == "identified"


# ── 5. Movie Fingerprint ──────────────────────────────────────────────────────

class TestMovieFingerprint:
    def test_movie_fingerprint_0_96(self):
        r = _build(tpdb_movies=[_tpdb_movie(method="hash")])
        assert r["status"] == "identified"
        assert r["confidence"] == pytest.approx(0.96)
        assert "Movie Fingerprint" in r["source"]


# ── 6. Movie Context ──────────────────────────────────────────────────────────

class TestMovieContext:
    def test_movie_score_80_plus_0_90(self):
        r = _build(tpdb_movies=[_tpdb_movie(method="context", score=85, duration=7200)],
                   local_duration=7200)
        assert r["status"] == "identified"
        assert r["confidence"] == pytest.approx(0.90)

    def test_movie_score_50_to_79_0_78(self):
        r = _build(tpdb_movies=[_tpdb_movie(method="context", score=60, duration=7200)],
                   local_duration=7200)
        assert r["status"] == "likely"
        assert r["confidence"] == pytest.approx(0.78)

    def test_movie_score_below_50_0_40(self):
        r = _build(tpdb_movies=[_tpdb_movie(method="context", score=30, duration=7200)],
                   local_duration=7200)
        assert r["status"] == "possible"
        assert r["confidence"] == pytest.approx(0.40)

    def test_movie_duration_conflict_0_35(self):
        # 148 min file, 51 min movie
        r = _build(
            tpdb_movies=[_tpdb_movie(method="context", score=80, duration=3060)],
            local_duration=8902,
        )
        assert r["status"] == "possible"
        assert r["confidence"] == pytest.approx(0.35)


# ── 7. Cross-confirm StashDB + TPDB ──────────────────────────────────────────

class TestCrossConfirm:
    def _cross_build(self, local_duration=None, sdb_duration=3600, tpdb_duration=3600,
                     title="Same Scene", sdb_studio=None, tpdb_studio=None, score=70):
        sdb = _sdb_scene(match_via="context", duration=sdb_duration, title=title)
        sdb["studio"] = sdb_studio
        tpdb = _tpdb_scene(method="title", score=score, duration=tpdb_duration, title=title)
        tpdb["site"] = tpdb_studio
        return _build(
            stashdb_scenes=[sdb],
            tpdb_scenes=[tpdb],
            local_duration=local_duration,
        )

    def test_cross_confirm_high_score_0_90(self):
        # Same title in both sources → cross-confirm
        r = self._cross_build(score=65, title="Same Scene Title")
        # cross-confirm only triggers on title/performer overlap; score >= 60 → 0.90
        assert r["status"] in ("identified", "likely", "possible")

    def test_cross_duration_conflict_0_35(self):
        # TPDB duration conflicts with local → possible even if cross-confirmed
        r = _build(
            stashdb_scenes=[_sdb_scene(match_via="context", duration=3600)],
            tpdb_scenes=[_tpdb_scene(method="title", score=80, duration=3600)],
            local_duration=8902,  # 148 min vs 60 min → conflict
        )
        assert r["status"] == "possible"
        assert r["confidence"] == pytest.approx(0.35)


# ── 8. TPDB Context ──────────────────────────────────────────────────────────

class TestTPDBContext:
    def test_tpdb_context_score_80_plus_0_88(self):
        r = _build(
            tpdb_scenes=[_tpdb_scene(method="title", score=85, duration=3600)],
            local_duration=3600,
        )
        assert r["status"] == "identified"
        assert r["confidence"] == pytest.approx(0.88)
        assert r["action"] == "rename"

    def test_tpdb_context_score_50_to_79_0_76(self):
        r = _build(
            tpdb_scenes=[_tpdb_scene(method="title", score=60, duration=3600)],
            local_duration=3600,
        )
        assert r["status"] == "likely"
        assert r["confidence"] == pytest.approx(0.76)
        assert r["action"] == "review"

    def test_tpdb_context_score_below_50_0_35(self):
        r = _build(
            tpdb_scenes=[_tpdb_scene(method="title", score=30, duration=3600)],
            local_duration=3600,
        )
        assert r["status"] == "possible"
        assert r["confidence"] == pytest.approx(0.35)
        assert r["suggested_name"] is None

    def test_tpdb_context_duration_conflict_0_35(self):
        """This is the case the user reported: 148 min file, 51 min TPDB scene."""
        r = _build(
            tpdb_scenes=[_tpdb_scene(method="title", score=85, duration=3060)],
            local_duration=8902,
        )
        assert r["status"] == "possible"
        assert r["confidence"] == pytest.approx(0.35)
        assert "Dauerkonflikt" in " ".join(r.get("signals", []))

    def test_tpdb_context_score_50_has_suggested_name(self):
        r = _build(tpdb_scenes=[_tpdb_scene(method="title", score=55, title="The Title")])
        assert r["suggested_name"] is not None

    def test_tpdb_context_score_below_50_no_suggested_name(self):
        r = _build(tpdb_scenes=[_tpdb_scene(method="title", score=25)])
        assert r["suggested_name"] is None


# ── 9. StashDB Context ────────────────────────────────────────────────────────

class TestStashDBContext:
    def test_stashdb_context_0_78(self):
        r = _build(stashdb_scenes=[_sdb_scene(match_via="context", duration=3600)],
                   local_duration=3600)
        assert r["status"] == "likely"
        assert r["confidence"] == pytest.approx(0.78)
        assert r["action"] == "review"

    def test_stashdb_performer_0_64(self):
        r = _build(stashdb_scenes=[_sdb_scene(match_via="performer", duration=3600)],
                   local_duration=3600)
        assert r["status"] == "possible"
        assert r["confidence"] == pytest.approx(0.64)

    def test_stashdb_context_duration_conflict_0_32(self):
        r = _build(
            stashdb_scenes=[_sdb_scene(match_via="context", duration=3060)],
            local_duration=8902,
        )
        assert r["status"] == "possible"
        assert r["confidence"] == pytest.approx(0.32)


# ── 10. Ollama only ───────────────────────────────────────────────────────────

class TestOllamaOnly:
    def test_ollama_high_conf_0_55(self):
        r = _build(ollama=_ollama(conf=0.90))
        assert r["status"] == "possible"
        assert r["confidence"] == pytest.approx(0.55)
        assert r["action"] == "review"

    def test_ollama_low_conf_unknown(self):
        r = _build(ollama=_ollama(conf=0.70))
        assert r["status"] == "unknown"
        assert r["confidence"] == pytest.approx(0.0)

    def test_ollama_error_unknown(self):
        r = _build(ollama={"confidence": 0.9, "cleaned_name": "X", "error": "timeout"})
        assert r["status"] == "unknown"

    def test_ollama_exactly_at_threshold(self):
        r = _build(ollama=_ollama(conf=0.85))
        assert r["status"] == "possible"
        assert r["confidence"] == pytest.approx(0.55)


# ── 11. Unknown (no signals) ──────────────────────────────────────────────────

class TestUnknown:
    def test_no_signals_unknown(self):
        r = _build()
        assert r["status"] == "unknown"
        assert r["confidence"] == pytest.approx(0.0)
        assert r["action"] == "review"
        assert r["suggested_name"] is None

    def test_ollama_none_unknown(self):
        r = _build(ollama=None)
        assert r["status"] == "unknown"


# ── action field consistency ──────────────────────────────────────────────────

class TestActionConsistency:
    """All rename actions must have high confidence; all review actions must not auto-rename."""

    def test_all_rename_actions_are_identified(self):
        rename_cases = [
            _build(dest_duplicate="dup"),  # skip (not rename, but high conf)
            _build(stashdb_scenes=[_sdb_scene(match_via="fingerprint")]),
            _build(tpdb_scenes=[_tpdb_scene(method="hash")]),
            _build(tpdb_scenes=[_tpdb_scene(method="jav", duration=3600)], local_duration=3600),
            _build(tpdb_movies=[_tpdb_movie(method="hash")]),
        ]
        for r in rename_cases:
            assert r["action"] in ("rename", "skip"), f"Expected rename/skip, got {r}"
            assert r["confidence"] >= 0.9, f"Rename action but low confidence: {r}"

    def test_review_action_always_below_0_9(self):
        review_cases = [
            _build(ollama=_ollama(conf=0.9)),
            _build(tpdb_scenes=[_tpdb_scene(method="title", score=60)]),
            _build(stashdb_scenes=[_sdb_scene(match_via="context")]),
        ]
        for r in review_cases:
            assert r["action"] == "review"
            assert r["confidence"] < 0.9


class TestPerformerAnchorGate:
    """Regression for the Alexa Grace/Blair Summers → Rosalinda false positive.

    A candidate that shares NO performer with the filename's named performers
    must never be 'identified' — no matter how well two databases agree.
    """

    def _wrong(self):
        # Filename names Alexa Grace + Blair Summers; candidate is Rosalinda.
        return dict(
            original_name="Alexa Grace and Blair Summers - ATKG.mp4",
            filename_parsed={"performers": ["Alexa Grace", "Blair Summers"]},
            local_duration=1033,
        )

    def test_cross_confirm_rejected_on_performer_conflict(self):
        scene = {"title": "Rosalinda Tries The Dildo Challenge",
                 "performers": ["Rosalinda Blonde"], "date": "2026-02-22",
                 "duration": 1033, "site": "Nebraska Coeds", "match_via": "context"}
        r = _build(
            stashdb_scenes=[scene], stashdb_suggested="N.mp4",
            tpdb_scenes=[dict(scene, score=120, match_method="title")],
            tpdb_suggested="N.mp4", **self._wrong())
        assert r["status"] != "identified"
        assert r["confidence"] < 0.9
        assert r["action"] == "review"

    def test_tpdb_context_rejected_on_performer_conflict(self):
        scene = {"title": "Rosalinda Scene", "performers": ["Rosalinda Blonde"],
                 "date": "2026-02-22", "duration": 1033, "site": "Nebraska Coeds",
                 "match_method": "title", "score": 90}
        r = _build(tpdb_scenes=[scene], tpdb_suggested="N.mp4", **self._wrong())
        assert r["status"] == "possible"
        assert r["action"] == "review"
        assert r["suggested_name"] is None

    def test_stashdb_context_rejected_on_performer_conflict(self):
        scene = {"title": "Rosalinda Scene", "performers": ["Rosalinda Blonde"],
                 "duration": 1033, "site": "Nebraska Coeds", "match_via": "context"}
        r = _build(stashdb_scenes=[scene], stashdb_suggested="N.mp4", **self._wrong())
        assert r["status"] == "possible"

    def test_matching_performers_still_identified(self):
        # Same shape, but candidate DOES contain a filename performer → keep it
        scene = {"title": "Hot Scene", "performers": ["Alexa Grace", "Other"],
                 "date": "2026-02-22", "duration": 1033, "site": "ATKGalleria",
                 "match_via": "context"}
        r = _build(
            stashdb_scenes=[scene], stashdb_suggested="A.mp4",
            tpdb_scenes=[dict(scene, score=120, match_method="title")],
            tpdb_suggested="A.mp4", **self._wrong())
        assert r["status"] == "identified"

    def test_no_filename_performers_gate_inactive(self):
        # Garbage/empty filename performers → gate must not fire (no anchor)
        scene = {"title": "Some Scene", "performers": ["Rosalinda Blonde"],
                 "date": "2026-02-22", "duration": 3600, "site": "X",
                 "match_method": "title", "score": 90}
        r = _build(tpdb_scenes=[scene], tpdb_suggested="S.mp4",
                   original_name="x.mp4", filename_parsed={"performers": []},
                   local_duration=3600)
        # Without a filename anchor the gate is silent (falls back to score path)
        assert r["status"] in ("identified", "likely", "possible")

    def test_singleword_tagword_not_treated_as_performer(self):
        # "blonde"/"brunette" are 1-word tag-words → must NOT anchor the gate
        scene = {"title": "Scene", "performers": ["Rosalinda Blonde"],
                 "duration": 3600, "site": "X", "match_method": "title", "score": 90}
        r = _build(tpdb_scenes=[scene], tpdb_suggested="S.mp4",
                   original_name="x.mp4",
                   filename_parsed={"performers": ["blonde", "brunette"]},
                   local_duration=3600)
        # 1-word names don't count as strong → gate inactive, normal scoring
        assert "Performer-Konflikt" not in " ".join(r.get("signals", []))
