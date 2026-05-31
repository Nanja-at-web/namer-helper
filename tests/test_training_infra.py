from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_c1_training_infrastructure_files_exist():
    assert (ROOT / "training" / "README.md").exists()
    assert (ROOT / "training" / "train.sh").exists()
    assert (ROOT / "modelfiles" / "scene-parser.Modelfile").exists()


def test_c1_training_script_is_guarded():
    text = (ROOT / "training" / "train.sh").read_text(encoding="utf-8")

    assert "CUDA is required" in text
    assert "Qwen/Qwen2.5-1.5B-Instruct" in text
    assert "Missing training dependency" in text


def test_scene_parser_modelfile_is_conservative():
    text = (ROOT / "modelfiles" / "scene-parser.Modelfile").read_text(encoding="utf-8")

    assert "Nicht raten" in text
    assert "Keine neuen Performer" in text
    assert "temperature 0.05" in text

