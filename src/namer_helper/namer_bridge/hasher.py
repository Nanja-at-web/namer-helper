"""
Compute video file fingerprints and extract technical metadata locally.

oshash: OpenSubtitles hash — fast, no dependencies, supported by StashDB.
get_video_info: FFProbe-based — duration, resolution, embedded tags.
"""

from __future__ import annotations

import json
import struct
import subprocess
from pathlib import Path


def compute_oshash(path: Path) -> str | None:
    """
    OpenSubtitles hash: XOR-sum of first+last 64 KB chunks plus file size.
    Returns 16-char lowercase hex string, or None on failure.
    """
    try:
        size = path.stat().st_size
        if size < 131072:  # file too small to produce a reliable hash
            return None
        chunk = 65536
        hash_value = size
        with open(path, "rb") as f:
            for _ in range(2):
                data = f.read(chunk)
                for i in range(0, len(data) - 7, 8):
                    (word,) = struct.unpack_from("<q", data, i)
                    hash_value = (hash_value + word) & 0xFFFFFFFFFFFFFFFF
                f.seek(-chunk, 2)
        return format(hash_value, "016x")
    except OSError:
        return None


def detect_studio_logo(path: Path, ollama_url: str, model: str = "moondream") -> str | None:
    """
    Extract studio/network logo from video intro frames via Ollama vision model.

    Checks timestamps 1s, 3s, 8s — intro frames typically contain logo cards.
    Returns the first non-empty, non-generic answer (max 60 chars), or None.
    """
    import base64
    import os
    import tempfile

    try:
        import requests
    except ImportError:
        return None

    prompt = (
        "What studio logo, watermark or brand name is visible in this image? "
        "Reply with ONLY the name (e.g. 'Brazzers', 'Tushy', 'Pure Taboo'). "
        "If nothing is visible reply with: none"
    )
    _skip = {"none", "no", "nothing", "n/a", "unknown", ""}

    try:
        duration = get_video_info(path).get("duration") or 0
        timestamps = [2, 8]
        if duration and duration > 120:
            timestamps.extend([max(1, int(duration * 0.25)), max(1, int(duration * 0.55)), max(1, int(duration * 0.85))])

        with tempfile.TemporaryDirectory() as tmp:
            for idx, ts in enumerate(dict.fromkeys(timestamps)):
                frame = os.path.join(tmp, f"logo_{idx}.jpg")
                r = subprocess.run(
                    ["ffmpeg", "-ss", str(ts), "-i", str(path),
                     "-vframes", "1", "-q:v", "2", frame],
                    capture_output=True, timeout=10,
                )
                if r.returncode != 0 or not os.path.exists(frame):
                    continue
                img_b64 = base64.b64encode(open(frame, "rb").read()).decode()
                try:
                    resp = requests.post(
                        f"{ollama_url.rstrip('/')}/api/generate",
                        json={"model": model, "prompt": prompt,
                              "images": [img_b64], "stream": False},
                        timeout=90,
                    )
                    ans = resp.json().get("response", "").strip()
                    ans = ans.splitlines()[0].lstrip("-•* ").strip() if ans else ""
                    if ans and ans.lower() not in _skip and len(ans) <= 60:
                        return ans
                except Exception:
                    pass
    except Exception:
        pass
    return None


def compute_phash(path: Path, duration: int | None = None) -> str | None:
    """
    Compute perceptual hash (phash) via FFmpeg frame extraction + imagehash.

    Extracts a frame at 30% of video duration, computes 64-bit phash.
    Compatible with StashDB/TPDB PHASH fingerprint format.
    Returns 16-char hex string or None on failure.
    """
    try:
        import io
        import imagehash
        from PIL import Image

        timestamp = max(1, int((duration or 60) * 0.3))
        result = subprocess.run(
            [
                "ffmpeg", "-ss", str(timestamp), "-i", str(path),
                "-vframes", "1", "-f", "image2pipe", "-vcodec", "png", "-",
            ],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0 or not result.stdout:
            return None

        img = Image.open(io.BytesIO(result.stdout))
        h = imagehash.phash(img, hash_size=8)
        return format(int(str(h), 16), "016x")
    except Exception:
        return None


def extract_frame_text(path: Path, timestamps: list[int] | None = None, duration: int | None = None) -> str:
    """
    Extract visible text from video frames via FFmpeg + Tesseract (TSV mode).

    Preprocesses each frame (grayscale → 2x upscale → invert if dark).
    Samples intro (1s, 3s, 5s, 8s) plus outro at 90% of duration if known.
    Hard timeout: 20 s total across all frames so this never blocks a request.
    Returns deduplicated lines with ≥5 letters and ≥2 words. Empty string on failure.
    Returns empty string if ffmpeg or tesseract are not installed.
    """
    import os
    import tempfile
    import time

    try:
        return _extract_frame_text_inner(path, timestamps, duration)
    except (FileNotFoundError, OSError):
        return ""


def _extract_frame_text_inner(path: Path, timestamps: list[int] | None, duration: int | None) -> str:
    import os
    import tempfile
    import time

    deadline = time.monotonic() + 20  # 20 s hard cap across all frames

    if timestamps is None:
        timestamps = [1, 3, 5, 8]
        if duration and duration > 30:
            timestamps.append(int(duration * 0.9))

    try:
        from PIL import Image, ImageEnhance, ImageOps
        has_pil = True
    except ImportError:
        has_pil = False

    def _tsv_lines(tsv_output: str) -> list[str]:
        line_words: dict[tuple, list[str]] = {}
        for row in tsv_output.splitlines()[1:]:
            parts = row.split("\t")
            if len(parts) < 12:
                continue
            try:
                conf = float(parts[10])
            except ValueError:
                continue
            word = parts[11].strip()
            if conf < 60 or len(word) < 2:
                continue
            key = (parts[1], parts[2], parts[3], parts[4])
            line_words.setdefault(key, []).append(word)
        result = []
        for words in line_words.values():
            line = " ".join(words)
            letters = sum(c.isalpha() for c in line)
            if letters >= 5 and len(words) >= 2 and len(line) <= 80:
                result.append(line)
        return result

    collected: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for ts in timestamps:
            if time.monotonic() > deadline:
                break
            raw = os.path.join(tmp, f"frame_{ts}.png")
            r = subprocess.run(
                ["ffmpeg", "-ss", str(ts), "-i", str(path),
                 "-vframes", "1", "-q:v", "2", raw],
                capture_output=True, timeout=10,
            )
            if r.returncode != 0 or not os.path.exists(raw):
                continue

            proc = raw
            if has_pil:
                try:
                    img = Image.open(raw).convert("L")
                    pixels = list(img.getdata())
                    mean = sum(pixels) / len(pixels)
                    img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
                    if mean < 110:
                        img = ImageOps.invert(img)
                    img = ImageEnhance.Contrast(img).enhance(2.0)
                    proc = os.path.join(tmp, f"frame_{ts}_proc.png")
                    img.save(proc)
                except Exception:
                    proc = raw

            for psm in ("11", "6"):
                if time.monotonic() > deadline:
                    break
                remaining = max(3, int(deadline - time.monotonic()))
                ocr = subprocess.run(
                    ["tesseract", proc, "stdout", "--psm", psm, "--oem", "3",
                     "-l", "eng", "tsv"],
                    capture_output=True, text=True, timeout=remaining,
                )
                if ocr.returncode != 0:
                    continue
                collected.extend(_tsv_lines(ocr.stdout))

    seen: set[str] = set()
    result: list[str] = []
    for line in collected:
        key = line.lower()
        if key not in seen:
            seen.add(key)
            result.append(line)
    return "\n".join(result)


def get_video_info(path: Path) -> dict:
    """
    Extract technical metadata via FFProbe.
    Returns dict with: duration (int seconds), width, height,
    resolution_label, meta_title, meta_studio, meta_comment.
    Returns empty dict if ffprobe is unavailable or fails.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format", "-show_streams",
                str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return {}

    fmt = data.get("format", {})
    streams = data.get("streams", [])

    # Duration — prefer format-level, fall back to first video stream
    duration: int | None = None
    raw_dur = fmt.get("duration")
    if raw_dur:
        try:
            duration = round(float(raw_dur))
        except ValueError:
            pass

    # Resolution from first video stream
    width: int | None = None
    height: int | None = None
    for stream in streams:
        if stream.get("codec_type") == "video":
            width = stream.get("width")
            height = stream.get("height")
            if not duration:
                raw_sdur = stream.get("duration")
                if raw_sdur:
                    try:
                        duration = round(float(raw_sdur))
                    except ValueError:
                        pass
            break

    # Resolution label
    resolution_label = ""
    if height:
        if height >= 2160:
            resolution_label = "4K"
        elif height >= 1080:
            resolution_label = "1080p"
        elif height >= 720:
            resolution_label = "720p"
        elif height >= 480:
            resolution_label = "480p"
        else:
            resolution_label = f"{height}p"

    # Embedded metadata tags (case-insensitive lookup)
    tags = {k.lower(): v for k, v in fmt.get("tags", {}).items()}
    meta_title = tags.get("title", "")
    meta_studio = (
        tags.get("studio") or tags.get("publisher") or
        tags.get("artist") or tags.get("album_artist") or ""
    )
    meta_comment = tags.get("comment", "") or tags.get("description", "")
    meta_date = tags.get("date", "") or tags.get("year", "")
    meta_performers = (
        tags.get("performer") or tags.get("actor") or
        tags.get("composer") or ""
    )
    meta_copyright = tags.get("copyright", "")
    meta_encoded_by = tags.get("encoded_by", "") or tags.get("encoder", "")

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "resolution_label": resolution_label,
        "meta_title": meta_title,
        "meta_studio": meta_studio,
        "meta_comment": meta_comment,
        "meta_date": meta_date,
        "meta_performers": meta_performers,
        "meta_copyright": meta_copyright,
        "meta_encoded_by": meta_encoded_by,
    }
