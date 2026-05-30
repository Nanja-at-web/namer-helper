"""
Filename normalization — first pass before filename_parser or LLM.

Handles: URL-encoding, Unicode → ASCII, bracket noise, noise chars (#/*),
and leet-speak substitution. Does NOT strip technical tags (1080p, x264) —
that remains the job of analyzer._STRIP_RE or filename_parser._TECH_RE.

Leet substitution is adjacency-aware:
  - Interior char (both neighbors are alnum): replace only if BOTH are alpha.
  - Boundary char (start/end of token, or next to separator): replace if at
    least one neighbor is alpha.
  - Protected tokens (pure numbers, resolutions, codecs): never touched.

Examples:
  3v1l.4ng3l.mp4  →  evil.angel      (leet, separators preserved)
  pr0n.mp4        →  porn            (leet: 0 between two alpha chars)
  cu#t.avi        →  cut             (noise char removal)
  1080p.mkv       →  1080p           (protected — not leet-replaced)
  x264.mkv        →  x264            (protected — 2 in x264 not replaced)
  %C3%A9ve.mp4    →  eve             (URL-decode + unicode strip)
"""

from __future__ import annotations

import re
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path


LEET_MAP: dict[str, str] = {
    "0": "o", "1": "i", "3": "e", "4": "a",
    "5": "s", "7": "t", "8": "b", "9": "g",
    "@": "a", "$": "s", "!": "i", "+": "t",
}

# Tokens that are technical metadata — skip leet substitution entirely.
# Covers resolutions (4K, 1080p), codecs (x264), sources (BluRay), pure numbers.
_TECH_TOKEN_RE = re.compile(
    r"^(?:"
    r"4[Kk]|2160[piP]?|1080[piP]?|720[piP]?|480[piP]?|360[piP]?"
    r"|[Xx]26[45]|[Hh]26[45]|xvid|HEVC|AVC"
    r"|WEB-?DL|WEBRip|BluRay|Blu-Ray|HDTV|DVDRip|BDRip|BRRip|WEB"
    r"|AAC[\d.]*|AC3|DTS[\w-]*|MP3|FLAC"
    r"|REPACK|PROPER|RERIP|EXTENDED|UNRATED|UNCENSORED"
    r"|\d{2,}"   # pure multi-digit numbers: years, ep numbers, etc.
    r")$",
    re.IGNORECASE,
)

# Splits text into (token, separator, token, separator, …) pairs.
_SEP_RE = re.compile(r"([ .\-_]+)")

# Bracketed groups with short content — likely noise like (3), [720p], (copy).
_BRACKET_NOISE_RE = re.compile(r"[\(\[][^\)\]]{0,30}[\)\]]")

_VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".m4v"}


@dataclass
class NormalizeResult:
    original: str
    normalized: str       # cleaned stem — no extension, separators preserved
    ext: str              # lowercased extension including dot, e.g. ".mp4"
    leet_detected: bool
    noise_detected: bool
    steps: dict[str, str] = field(default_factory=dict)


def _leet_token(token: str) -> tuple[str, bool]:
    """Apply leet substitutions within one word token (no separators inside)."""
    if _TECH_TOKEN_RE.match(token):
        return token, False
    chars = list(token)
    detected = False
    n = len(token)
    for i, ch in enumerate(token):
        replacement = LEET_MAP.get(ch)
        if replacement is None:
            continue
        left_alpha  = i > 0     and token[i - 1].isalpha()
        right_alpha = i < n - 1 and token[i + 1].isalpha()
        left_alnum  = i > 0     and token[i - 1].isalnum()
        right_alnum = i < n - 1 and token[i + 1].isalnum()
        # A char is "interior" when both neighbors are alnum (no separator nearby).
        # Interior rule: require BOTH neighbors to be alpha (avoids 1080p → 108op).
        # Boundary rule: at least ONE neighbor must be alpha.
        interior = left_alnum and right_alnum
        if interior:
            if left_alpha and right_alpha:
                chars[i] = replacement
                detected = True
        else:
            if left_alpha or right_alpha:
                chars[i] = replacement
                detected = True
    return "".join(chars), detected


def _apply_leet(text: str) -> tuple[str, bool]:
    """Split on separators, apply leet per token, rejoin preserving separators."""
    parts = _SEP_RE.split(text)
    result: list[str] = []
    detected = False
    for part in parts:
        if _SEP_RE.fullmatch(part):
            result.append(part)
        else:
            leet_part, d = _leet_token(part)
            detected = detected or d
            result.append(leet_part)
    return "".join(result), detected


def normalize(filename: str) -> NormalizeResult:
    """
    Normalize a video filename stem for downstream parsing or LLM input.

    Returns NormalizeResult with the cleaned stem in .normalized and the
    original file extension in .ext.  Call .normalized on the result —
    it still has original separators (. _ -) so filename_parser or
    _STRIP_RE can process it normally afterwards.
    """
    steps: dict[str, str] = {}
    noise_detected = False
    original = filename

    # 1. URL-decode (%C3%A9 → é, %20 → space, …)
    decoded = urllib.parse.unquote(filename)
    if decoded != filename:
        steps["url_decode"] = decoded

    # 2. Unicode NFKD → ASCII  (é→e, ü→u, … drop combining chars)
    ascii_name = (
        unicodedata.normalize("NFKD", decoded)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    if ascii_name != decoded:
        steps["unicode"] = ascii_name

    # 3. Extract extension
    p = Path(ascii_name)
    ext = p.suffix.lower()
    stem = p.stem if ext in _VIDEO_EXTS else ascii_name
    if ext not in _VIDEO_EXTS:
        ext = ""

    # 4. Remove short bracketed noise: (3), [720p], (Kopie), (copy 2), …
    stem_before = stem
    stem = _BRACKET_NOISE_RE.sub(" ", stem)
    if stem != stem_before:
        noise_detected = True
        steps["brackets"] = re.sub(r" {2,}", " ", stem).strip()

    # 5. Remove bare noise chars # and *
    #    Remove without inserting a space so adjacent letters merge:
    #    cu#t → cut,  p*rn → prn  (LLM still recognises the latter)
    stem_before = stem
    stem = re.sub(r"[#*]", "", stem)
    if stem != stem_before:
        noise_detected = True
        steps["noise_chars"] = re.sub(r" {2,}", " ", stem).strip()

    # 6. Leet substitution  (run BEFORE tech-strip so we don't confuse digits)
    stem_leet, leet_detected = _apply_leet(stem)
    if leet_detected:
        steps["leet"] = re.sub(r" {2,}", " ", stem_leet).strip()
    stem = stem_leet

    return NormalizeResult(
        original=original,
        normalized=re.sub(r" {2,}", " ", stem).strip(),
        ext=ext,
        leet_detected=leet_detected,
        noise_detected=noise_detected,
        steps=steps,
    )
