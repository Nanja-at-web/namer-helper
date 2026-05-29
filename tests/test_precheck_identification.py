from namer_helper.web.identification import build_identification


def test_identification_prefers_stashdb_fingerprint():
    result = build_identification(
        original_name="scene_001.mp4",
        stashdb_scenes=[{
            "title": "Example Scene",
            "date": "2024-01-02",
            "studio": "Example Studio",
            "performers": ["Performer A"],
            "match_via": "fingerprint",
        }],
        stashdb_suggested="Example Studio - 2024-01-02 - Example Scene (Performer A).mp4",
        tpdb_scenes=[],
        tpdb_suggested=None,
        ollama=None,
        filename_parsed=None,
        dest_duplicate=None,
    )

    assert result["status"] == "identified"
    assert result["action"] == "rename"
    assert result["confidence"] >= 0.95
    assert result["source"] == "StashDB Fingerprint"


def test_identification_cross_confirms_context_sources():
    result = build_identification(
        original_name="scene_002.mkv",
        stashdb_scenes=[{
            "title": "Shared Example Title",
            "date": "2024-03-04",
            "studio": "Example Studio",
            "performers": ["Performer A", "Performer B"],
            "match_via": "context",
        }],
        stashdb_suggested="Example Studio - 2024-03-04 - Shared Example Title.mkv",
        tpdb_scenes=[{
            "title": "Shared Example Title",
            "date": "2024-03-04",
            "site": "Example Studio",
            "performers": ["Performer A", "Performer B"],
            "score": 70,
            "match_method": "title",
        }],
        tpdb_suggested="Example Studio - 2024-03-04 - Shared Example Title (Performer A, Performer B).mkv",
        ollama=None,
        filename_parsed={"performers": ["Performer A", "Performer B"]},
        dest_duplicate=None,
    )

    assert result["status"] == "identified"
    assert result["source"] == "StashDB + ThePornDB"
    assert result["confidence"] >= 0.86
    assert result["suggested_name"].endswith(".mkv")


def test_identification_keeps_ollama_only_as_review():
    result = build_identification(
        original_name="scene_003.mp4",
        stashdb_scenes=[],
        stashdb_suggested=None,
        tpdb_scenes=[],
        tpdb_suggested=None,
        ollama={"confidence": 0.95, "cleaned_name": "Maybe Title"},
        filename_parsed=None,
        dest_duplicate=None,
    )

    assert result["status"] == "possible"
    assert result["action"] == "review"
    assert result["confidence"] < 0.7



def test_identification_rejects_cross_confirmed_duration_mismatch():
    result = build_identification(
        original_name="movie_001.mp4",
        stashdb_scenes=[{
            "title": "Shared Movie Title",
            "date": "2024-01-02",
            "studio": "Studio A",
            "performers": ["Performer A", "Performer B"],
            "duration": 1306,
            "match_via": "context",
        }],
        stashdb_suggested="Studio A - 2024-01-02 - Shared Movie Title.mp4",
        tpdb_scenes=[{
            "title": "Shared Movie Title",
            "date": "2024-01-02",
            "site": "Different Studio",
            "performers": ["Performer A", "Performer B"],
            "duration": 1306,
            "score": 80,
            "match_method": "title",
        }],
        tpdb_suggested="Different Studio - 2024-01-02 - Shared Movie Title.mp4",
        ollama=None,
        filename_parsed={"performers": ["Performer A", "Performer B"]},
        dest_duplicate=None,
        local_duration=9106,
    )

    assert result["status"] == "possible"
    assert result["action"] == "review"
    assert result["confidence"] <= 0.35
    assert result["suggested_name"] is None
    assert any("Dauerkonflikt" in signal for signal in result["signals"])



def test_identification_accepts_tpdb_movie_context_match():
    result = build_identification(
        original_name="movie_002.mp4",
        stashdb_scenes=[],
        stashdb_suggested=None,
        tpdb_scenes=[],
        tpdb_suggested=None,
        tpdb_movies=[{
            "title": "Feature Movie",
            "date": "2020-05-06",
            "site": "Movie Studio",
            "performers": ["Performer A"],
            "duration": 9100,
            "score": 85,
            "match_method": "movie",
        }],
        tpdb_movie_suggested="Movie Studio - 2020-05-06 - Feature Movie.mp4",
        ollama=None,
        filename_parsed={"performers": ["Performer A"]},
        dest_duplicate=None,
        local_duration=9106,
    )

    assert result["status"] == "identified"
    assert result["source"] == "ThePornDB Movie"
    assert result["action"] == "rename"
    assert result["suggested_name"] == "Movie Studio - 2020-05-06 - Feature Movie.mp4"



def test_identification_hides_low_score_tpdb_scene_suggestion():
    result = build_identification(
        original_name="scene_004.mp4",
        stashdb_scenes=[],
        stashdb_suggested=None,
        tpdb_scenes=[{
            "title": "Weak Match",
            "date": "2020-01-01",
            "site": "Wrong Studio",
            "performers": [],
            "duration": None,
            "score": 20,
            "match_method": "title",
        }],
        tpdb_suggested="Wrong Studio - 2020-01-01 - Weak Match.mp4",
        ollama=None,
        filename_parsed=None,
        dest_duplicate=None,
        local_duration=9106,
    )

    assert result["status"] == "possible"
    assert result["confidence"] <= 0.4
    assert result["suggested_name"] is None
    assert result["action"] == "review"
