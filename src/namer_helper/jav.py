"""
JAV code detection — identifies Japanese Adult Video title codes.

Standard format:  STUDIO-NNN
  STUDIO  2-6 uppercase letters  (ABP, MIDV, IPZZ, PRED, …)
  NNN     2-4 digits             (conservatively capped at 4;
                                  real codes max around 3 digits — PRED-999)

FC2-PPV is a distinct format: FC2-PPV-NNNNNNN (6-8 digit number).

A confirmed JAV code is fed directly to the TPDB /jav endpoint, bypassing
the slower generic /scenes text search which has poor recall for coded titles.

Detection tolerates mixed-case input — codes are always returned uppercase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# Standard JAV codes.  \d{2,4} keeps ABP-12 … ABP-9999 and rejects ABP-12345.
# Extend to \d{2,5} only if a confirmed 5-digit code is found in the wild.
_JAV_RE = re.compile(r"\b([A-Z]{2,6}-\d{2,4})\b", re.IGNORECASE)

# FC2-PPV: distinct structure — letter prefix followed by 6-8 digit number.
_FC2_RE = re.compile(r"\b(FC2-PPV-\d{6,8})\b", re.IGNORECASE)


@dataclass
class JavCode:
    code: str       # full code, uppercase — "ABP-123" or "FC2-PPV-1234567"
    studio: str     # letter prefix — "ABP" or "FC2"
    number: str     # digit suffix — "123" or "1234567"
    is_fc2: bool = False


def detect(filename: str) -> JavCode | None:
    """Return the first JAV code found in a filename, or None.

    FC2-PPV is checked before the generic pattern so it is never split
    into a false 'FC2-???' match.
    """
    m = _FC2_RE.search(filename)
    if m:
        code = m.group(1).upper()
        parts = code.split("-")
        return JavCode(code=code, studio="FC2", number=parts[-1], is_fc2=True)

    m = _JAV_RE.search(filename)
    if m:
        code = m.group(1).upper()
        studio, number = code.split("-", 1)
        return JavCode(code=code, studio=studio, number=number)

    return None


def detect_all(filename: str) -> list[JavCode]:
    """Return all JAV codes in a filename, in order of appearance."""
    seen: set[str] = set()
    entries: list[tuple[int, JavCode]] = []

    for m in _FC2_RE.finditer(filename):
        code = m.group(1).upper()
        if code not in seen:
            seen.add(code)
            parts = code.split("-")
            entries.append((m.start(), JavCode(
                code=code, studio="FC2", number=parts[-1], is_fc2=True,
            )))

    for m in _JAV_RE.finditer(filename):
        code = m.group(1).upper()
        if code not in seen:
            seen.add(code)
            studio, number = code.split("-", 1)
            entries.append((m.start(), JavCode(code=code, studio=studio, number=number)))

    entries.sort(key=lambda x: x[0])
    return [jav for _, jav in entries]
