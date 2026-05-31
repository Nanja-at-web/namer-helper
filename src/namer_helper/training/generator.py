"""
Generate synthetic training JSONL from confirmed rule-learning decisions.

The generator only uses user-confirmed target names as labels.  It creates
obfuscated filename variants as inputs, but never invents scene metadata.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, asdict
from pathlib import Path

from namer_helper.rules import Rule, load_rules


INSTRUCTION = (
    "Bereinige den verrauschten Videodateinamen und gib exakt den "
    "bestätigten kanonischen Dateinamen zurück."
)

_TECH_TAGS = [
    "1080p", "720p", "480p", "x264", "x265", "WEBRip", "AAC", "MP4",
    "hjav.in", "Jav Guru", "www.22366.com",
]
_NOISE_TAGS = [
    "#bigtits", "#hardcore", "#jav", "#pov", "watch online", "full movie",
    "porn hd", "free", "sample",
]
_LEET = str.maketrans({
    "a": "4", "e": "3", "i": "1", "o": "0", "s": "5", "t": "7",
    "A": "4", "E": "3", "I": "1", "O": "0", "S": "5", "T": "7",
})


@dataclass(frozen=True)
class TrainingExample:
    instruction: str
    input: str
    output: str
    source: str
    metadata: dict

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


def _stem_and_ext(name: str) -> tuple[str, str]:
    path = Path(name)
    ext = path.suffix or ".mp4"
    stem = path.stem if path.suffix else name
    return stem.strip(), ext


def _separator_variant(value: str, sep: str) -> str:
    cleaned = re.sub(r"\s*-\s*", sep, value)
    cleaned = re.sub(r"[\s_]+", sep, cleaned)
    return re.sub(re.escape(sep) + r"+", sep, cleaned).strip(sep)


def _drop_some_words(value: str, rng: random.Random) -> str:
    parts = [p for p in re.split(r"(\W+)", value) if p]
    word_indexes = [i for i, p in enumerate(parts) if re.search(r"[A-Za-z0-9]", p)]
    if len(word_indexes) <= 4:
        return value
    for idx in rng.sample(word_indexes, k=max(1, min(3, len(word_indexes) // 6))):
        parts[idx] = ""
    return "".join(parts).strip()


def _leet_some(value: str, rng: random.Random) -> str:
    chars = []
    for ch in value:
        if ch.lower() in "aeiost" and rng.random() < 0.25:
            chars.append(ch.translate(_LEET))
        else:
            chars.append(ch)
    return "".join(chars)


def variants_for_name(name: str, *, count: int = 12, seed: int = 0) -> list[str]:
    """Return deterministic noisy filename variants for a confirmed target name."""
    stem, ext = _stem_and_ext(name)
    rng = random.Random(f"{seed}:{name}")
    candidates: list[str] = [
        f"{stem}{ext}",
        f"{stem.lower()}{ext}",
        f"{_separator_variant(stem, '.')}{ext}",
        f"{_separator_variant(stem, '_')}{ext}",
        f"{_separator_variant(stem, ' ')} {rng.choice(_TECH_TAGS)}{ext}",
        f"{stem} {rng.choice(_NOISE_TAGS)} {rng.choice(_TECH_TAGS)}{ext}",
        f"{rng.choice(_TECH_TAGS)} {stem}{ext}",
        f"{_drop_some_words(stem, rng)} {rng.choice(_TECH_TAGS)}{ext}",
        f"{_leet_some(stem, rng)}{ext}",
        f"{_separator_variant(_leet_some(stem, rng), '.')}.{rng.choice(_TECH_TAGS)}{ext}",
    ]

    while len(candidates) < count * 2:
        base = rng.choice([
            stem,
            stem.lower(),
            _separator_variant(stem, rng.choice([".", "_", " "])),
            _drop_some_words(stem, rng),
            _leet_some(stem, rng),
        ])
        prefix = rng.choice(["", "", rng.choice(_NOISE_TAGS) + " "])
        suffix = rng.choice(["", " " + rng.choice(_TECH_TAGS), " " + rng.choice(_NOISE_TAGS)])
        candidates.append(f"{prefix}{base}{suffix}{ext}")

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = re.sub(r"\s+", " ", candidate).strip()
        key = normalized.lower()
        if key and key not in seen and normalized != name:
            seen.add(key)
            unique.append(normalized)
        if len(unique) >= count:
            break
    return unique


def examples_from_rules(
    rules: list[Rule],
    *,
    variants_per_rule: int = 12,
    seed: int = 0,
) -> list[TrainingExample]:
    examples: list[TrainingExample] = []
    for rule in rules:
        if rule.type != "hash" or not rule.suggested_name:
            continue
        metadata = {
            "oshash": rule.oshash,
            "tpdb_id": rule.tpdb_id,
            "created": rule.created,
        }
        for variant in variants_for_name(rule.suggested_name, count=variants_per_rule, seed=seed):
            examples.append(TrainingExample(
                instruction=INSTRUCTION,
                input=variant,
                output=rule.suggested_name,
                source=rule.source,
                metadata=metadata,
            ))
    return examples


def generate_from_rules_file(
    rules_path: Path,
    output_path: Path,
    *,
    variants_per_rule: int = 12,
    seed: int = 0,
) -> int:
    rules = load_rules(rules_path)
    examples = examples_from_rules(rules, variants_per_rule=variants_per_rule, seed=seed)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "\n".join(example.to_json() for example in examples) + ("\n" if examples else ""),
        encoding="utf-8",
    )
    return len(examples)

