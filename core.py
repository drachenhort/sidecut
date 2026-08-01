"""Framework-agnostic FLAC->MP3 conversion logic, shared by the GUI."""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TextIO

from mutagen.flac import FLAC
from mutagen.id3 import APIC, COMM, ID3, TALB, TCOM, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TXXX

QUALITY_PRESETS: dict[str, list[str]] = {
    "v0": ["-q:a", "0"],
    "v2": ["-q:a", "2"],
    "cbr320": ["-b:a", "320k"],
}

QUALITY_LABELS: dict[str, str] = {
    "v0": "V0 VBR (~245kbps)",
    "v2": "V2 VBR (~190kbps)",
    "cbr320": "320kbps CBR",
}

# Vorbis comment keys with a well-known ID3v2 frame equivalent. Everything
# else (AcoustID, MusicBrainz, ReplayGain, and any other custom tag) is kept
# verbatim as a TXXX frame so no tag is ever silently dropped.
_STANDARD_FRAME_BUILDERS: dict[str, Callable[[str], object]] = {
    "title": lambda v: TIT2(encoding=3, text=v),
    "artist": lambda v: TPE1(encoding=3, text=v),
    "album": lambda v: TALB(encoding=3, text=v),
    "albumartist": lambda v: TPE2(encoding=3, text=v),
    "date": lambda v: TDRC(encoding=3, text=v),
    "year": lambda v: TDRC(encoding=3, text=v),
    "genre": lambda v: TCON(encoding=3, text=v),
    "tracknumber": lambda v: TRCK(encoding=3, text=v),
    "discnumber": lambda v: TPOS(encoding=3, text=v),
    "composer": lambda v: TCOM(encoding=3, text=v),
    "comment": lambda v: COMM(encoding=3, lang="eng", desc="", text=v),
}


@dataclass
class ConversionResult:
    source: Path
    ok: bool
    message: str = ""
    src_bytes: int = 0
    dst_bytes: int = 0


@dataclass
class Progress:
    """One update of a running conversion's state, passed to a callback."""

    out_time_seconds: float = 0.0
    speed: str = ""
    percent: float = 0.0


def check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def find_flac_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".flac")


def track_duration(path: Path) -> float | None:
    try:
        return FLAC(path).info.length
    except Exception:  # noqa: BLE001 - duration is cosmetic, never fatal
        return None


def _copy_pictures(flac_tags: FLAC, id3: ID3) -> None:
    """Copy embedded cover art (front/back/etc.) as ID3 APIC frames.
    APIC frames are keyed by (type, desc); dedupe so multiple pictures of
    the same type don't silently overwrite each other."""
    seen: set[tuple[int, str]] = set()
    for i, picture in enumerate(flac_tags.pictures):
        desc = picture.desc or ""
        if (picture.type, desc) in seen:
            desc = f"{desc} {i}".strip()
        seen.add((picture.type, desc))
        id3.add(APIC(encoding=3, mime=picture.mime, type=picture.type, desc=desc, data=picture.data))


def copy_tags(src: Path, dst: Path) -> None:
    """Copy every FLAC Vorbis comment and embedded picture onto the MP3,
    mapping well-known tag keys to standard ID3v2.3 frames and preserving
    everything else (including acoustic/fingerprint tags like ACOUSTID_*)
    as TXXX frames; cover art is copied as APIC frames."""
    flac_tags = FLAC(src)
    id3 = ID3()
    for key, values in flac_tags.items():
        joined = "; ".join(values)
        builder = _STANDARD_FRAME_BUILDERS.get(key.lower())
        frame = builder(joined) if builder else TXXX(encoding=3, desc=key, text=joined)
        id3.add(frame)
    _copy_pictures(flac_tags, id3)
    id3.save(dst, v2_version=3)


def _apply_progress_line(progress: Progress, line: str, total_duration: float | None) -> None:
    """Parse one `-progress pipe:1` key=value line into a Progress update."""
    key, sep, value = line.partition("=")
    if not sep:
        return
    if key == "out_time_ms":
        with contextlib.suppress(ValueError):
            progress.out_time_seconds = int(value) / 1_000_000
    elif key == "speed":
        progress.speed = value.strip()
    if total_duration:
        progress.percent = min(100.0, progress.out_time_seconds / total_duration * 100)


def run_ffmpeg(
    src: Path,
    dst: Path,
    quality_args: list[str],
    log: TextIO,
    total_duration: float | None = None,
    on_progress: Callable[[Progress], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> bool:
    if should_cancel is not None and should_cancel():
        return False
    cmd = [
        "ffmpeg", "-y", "-nostdin", "-loglevel", "error", "-nostats",
        "-i", str(src), "-map", "0:a", "-map_metadata", "-1", *quality_args,
        "-progress", "pipe:1", str(dst),
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
    )
    assert proc.stdout is not None
    progress = Progress()
    for line in proc.stdout:
        if should_cancel is not None and should_cancel():
            proc.terminate()
            break
        _apply_progress_line(progress, line.strip(), total_duration)
        if on_progress is not None:
            on_progress(progress)
    stderr = proc.stderr.read() if proc.stderr else ""
    proc.wait()
    if stderr:
        log.write(stderr)
    return proc.returncode == 0


def convert_one(
    src: Path,
    quality_args: list[str],
    log: TextIO,
    on_progress: Callable[[Progress], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> ConversionResult:
    dst = src.with_suffix(".mp3")
    src_bytes = src.stat().st_size
    duration = track_duration(src)
    ok = run_ffmpeg(src, dst, quality_args, log, duration, on_progress, should_cancel)
    ok = ok and dst.is_file() and dst.stat().st_size > 0

    if ok:
        try:
            copy_tags(src, dst)
        except Exception as exc:  # noqa: BLE001 - any tag failure means "not done"
            ok = False
            log.write(f"tag copy failed for {src}: {exc}\n")

    if ok:
        dst_bytes = dst.stat().st_size
        src.unlink()
        return ConversionResult(src, True, src_bytes=src_bytes, dst_bytes=dst_bytes)

    dst.unlink(missing_ok=True)
    if should_cancel is not None and should_cancel():
        return ConversionResult(src, False, "cancelled")
    log.write(f"FAILED: {src}\n")
    return ConversionResult(src, False, "conversion failed, see log")
