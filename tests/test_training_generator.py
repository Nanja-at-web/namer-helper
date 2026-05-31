import json

from namer_helper.rules import Rule
from namer_helper.training.generator import (
    examples_from_rules,
    generate_from_rules_file,
    variants_for_name,
)


def test_variants_are_deterministic_and_noisy():
    name = "ROCKET - 2015-08-20 - RCT-769 - Example Title.mp4"

    first = variants_for_name(name, count=6, seed=3)
    second = variants_for_name(name, count=6, seed=3)

    assert first == second
    assert len(first) == 6
    assert name not in first
    assert any("1080p" in v or "Jav Guru" in v or "." in v for v in first)


def test_examples_from_rules_uses_confirmed_name_as_only_label():
    rule = Rule(
        type="hash",
        oshash="71cd356abee68aaa",
        suggested_name="ROCKET - 2015-08-20 - RCT-769 - Example Title.mp4",
        tpdb_id="scene-1",
        created="2026-05-31",
    )

    examples = examples_from_rules([rule], variants_per_rule=4, seed=1)

    assert len(examples) == 4
    assert {e.output for e in examples} == {rule.suggested_name}
    assert all(e.metadata["oshash"] == "71cd356abee68aaa" for e in examples)
    assert all(e.metadata["tpdb_id"] == "scene-1" for e in examples)


def test_generate_from_rules_file_writes_jsonl(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    out_path = tmp_path / "training.jsonl"
    rules_path.write_text(
        """
version: 1
rules:
  - type: hash
    oshash: 71cd356abee68aaa
    suggested_name: ROCKET - 2015-08-20 - RCT-769 - Example Title.mp4
    confidence: 1.0
    source: user_confirmed
    tpdb_id: scene-1
    created: '2026-05-31'
""",
        encoding="utf-8",
    )

    count = generate_from_rules_file(rules_path, out_path, variants_per_rule=3, seed=2)
    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines()]

    assert count == 3
    assert len(rows) == 3
    assert rows[0]["instruction"]
    assert rows[0]["output"] == "ROCKET - 2015-08-20 - RCT-769 - Example Title.mp4"
