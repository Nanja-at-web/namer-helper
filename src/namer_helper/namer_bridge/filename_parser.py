"""
Structured filename extraction — fast, deterministic pre-processing.

Strips technical noise, extracts date/resolution/studio/performers from
common video filename conventions.

normalize() is called early to resolve leet-speak and noise before the
structural parsing steps run. @mention and #hashtag markers are extracted
from the raw stem first (before normalize removes # as a noise char).

Used as a first pass before Ollama: the cleaned title is a much better
LLM input than the raw filename, and extracted date/performers are used
as direct search context for TPDB/StashDB scoring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from namer_helper.normalize import normalize as _normalize

if TYPE_CHECKING:
    from namer_helper.aliases import Aliases


_TECH_RE = re.compile(
    r'\b(?:'
    r'4[Kk]|2160[piP]|1080[piP]|720[piP]|480[piP]|360[piP]'
    r'|HDR\d*|SDR|FHD|UHD|HQ|HiDef|FullHD'
    r'|[Xx]26[45]|[Hh]26[45]|HEVC|AVC|xvid|DIVX|divx'
    r'|WEB-?DL|WEBRip|BluRay|Blu-Ray|HDTV|DVDRip|WEB|BDRip|BRRip'
    r'|AMZN|NF|DSNP|HULU|HBO|ATVP'
    r'|AAC[\d.]*|AC3|DTS[\w-]*|MP3|FLAC|EAC3|TrueHD'
    r'|REPACK|PROPER|RERIP|EXTENDED|UNRATED|UNCENSORED|INTERNAL'
    r'|\d+[Kk]bps|\d+fps'
    r')\b',
    re.IGNORECASE,
)

_DATE_RE = re.compile(
    r'(?P<y>\d{4})[.\-_](?P<m>\d{2})[.\-_](?P<d>\d{2})'
    r'|(?P<d2>\d{2})[.\-_](?P<m2>\d{2})[.\-_](?P<y2>\d{4})'
)

_RES_RE = re.compile(r'\b(4[Kk]|2160p|1080[piP]|720[piP]|480[piP])\b', re.IGNORECASE)

_BRACKET_RE = re.compile(r'[\[\(][^\]\)]{0,80}[\]\)]')

_MULTI_SPACE = re.compile(r'\s{2,}')


@dataclass
class FilenameInfo:
    cleaned: str = ""
    performers: list[str] = field(default_factory=list)
    studio: str | None = None
    date: str | None = None
    resolution: str | None = None
    tech_tags: list[str] = field(default_factory=list)
    confidence: float = 0.0


def parse_filename(name: str, aliases: "Aliases | None" = None) -> FilenameInfo:
    info = FilenameInfo()
    stem = Path(name).stem  # strip video extension

    # Resolution from original stem (checked before normalize alters case/content)
    res_m = _RES_RE.search(stem)
    if res_m:
        raw = res_m.group(1)
        info.resolution = "4K" if raw.upper() == "4K" else raw.lower()

    # Extract @mention/@hashtag markers from the RAW stem — normalize must not run
    # first because it removes '#' as a noise char, breaking hashtag detection.
    mentions = re.findall(r'@(\w[\w_]*)', stem)
    hashtags = re.findall(r'#(\w[\w_]*)', stem)
    if mentions:
        # Normalize the extracted studio value (resolves leet like @3v1l_4ng3l)
        raw_studio = mentions[0].replace('_', ' ')
        info.studio = _normalize(raw_studio).normalized.title()
    if hashtags:
        # Normalize each performer token (resolves leet initials/names)
        info.performers = [
            _normalize(t.replace('_', ' ')).normalized
            for t in hashtags[:5]
        ]
    work = re.sub(r'[#@]\w[\w_]*', ' ', stem)

    # Normalize the remaining work string: leet, noise chars, unicode.
    # work has no video extension at this point, so normalize treats it as a stem.
    work = _normalize(work).normalized

    # Date
    dm = _DATE_RE.search(work)
    if dm:
        g = dm.groupdict()
        if g.get('y'):
            info.date = f"{g['y']}-{g['m']}-{g['d']}"
        elif g.get('y2'):
            info.date = f"{g['y2']}-{g['m2']}-{g['d2']}"
        work = work[:dm.start()] + ' ' + work[dm.end():]

    # Strip technical tags, collect them
    def _grab(m: re.Match) -> str:
        info.tech_tags.append(m.group())
        return ' '
    work = _TECH_RE.sub(_grab, work)

    # Strip bracket content (often release group or tech info)
    work = _BRACKET_RE.sub(' ', work)

    # Normalize separators → spaces
    work = work.replace('_', ' ')
    work = re.sub(r'\.(?=\S)', ' ', work)   # dot between non-space → space
    work = work.replace('.', ' ')
    work = _MULTI_SPACE.sub(' ', work).strip()
    work = work.strip(' -–—')

    def _looks_like_performers(text: str) -> list[str]:
        """Return names if text looks like a comma/&/and-separated performer list."""
        parts = [n.strip() for n in re.split(r',|&|\band\b', text, flags=re.IGNORECASE) if n.strip()]
        if parts and all(1 <= len(n.split()) <= 3 and n[0].isupper() for n in parts):
            return parts
        return []

    # " - " segmented structure: Studio - Performers - Title
    for sep in (' - ', ' – ', ' — '):
        if sep not in work:
            continue
        segments = [s.strip().strip('-–—').strip() for s in work.split(sep) if s.strip()]
        if len(segments) == 2:
            # Check if first segment is actually a performer list (multiple names)
            first_as_perfs = _looks_like_performers(segments[0])
            if len(first_as_perfs) >= 2:
                info.performers = info.performers or first_as_perfs
            else:
                info.studio = info.studio or segments[0]
            work = segments[1]
        elif len(segments) == 3:
            mid = segments[1]
            # Check if first segment is performer list before assigning as studio
            first_as_perfs = _looks_like_performers(segments[0])
            mid_as_perfs = _looks_like_performers(mid)
            if len(first_as_perfs) >= 2 and not mid_as_perfs:
                # Pattern: "Perf1, Perf2 - Title - ..."
                info.performers = info.performers or first_as_perfs
                work = sep.join(segments[1:])
            else:
                info.studio = info.studio or segments[0]
                if mid_as_perfs:
                    info.performers = info.performers or mid_as_perfs
                    work = segments[2]
                else:
                    work = sep.join(segments[1:])
        elif len(segments) > 3:
            first_as_perfs = _looks_like_performers(segments[0])
            if len(first_as_perfs) >= 2:
                info.performers = info.performers or first_as_perfs
            else:
                info.studio = info.studio or segments[0]
            work = ' - '.join(segments[1:])
        break

    info.cleaned = _MULTI_SPACE.sub(' ', work).strip()

    # Resolve studio/performer abbreviations if an alias table was provided.
    if aliases is not None:
        from namer_helper.aliases import resolve_studio, resolve_performer
        if info.studio:
            info.studio = resolve_studio(info.studio, aliases)
        if info.performers:
            info.performers = [resolve_performer(p, aliases) for p in info.performers]

    # Confidence: how much structure did we find?
    score = 0.15
    if info.date:       score += 0.25
    if info.studio:     score += 0.20
    if info.performers: score += 0.20
    if info.tech_tags:  score += 0.10  # cleaned real noise
    if len(info.cleaned.split()) >= 2: score += 0.10
    info.confidence = round(min(score, 1.0), 2)

    return info
