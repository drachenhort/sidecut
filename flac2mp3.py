#!/usr/bin/env python3
"""Recursively transcode a folder of FLAC files to MP3, preserving all tags."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, TextIO

from mutagen.flac import FLAC
from mutagen.id3 import COMM, ID3, TALB, TCOM, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TXXX

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


def check_dependencies() -> None:
    for binary in ("ffmpeg",):
        if shutil.which(binary) is None:
            sys.exit(f"error: required tool '{binary}' not found on PATH")


def find_flac_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".flac")


def prompt_root_folder(default: Path) -> Path:
    while True:
        raw = input(f"Folder to scan recursively [{default}]: ").strip()
        candidate = Path(raw) if raw else default
        if candidate.is_dir():
            return candidate
        print(f"Not a directory: {candidate}")


def prompt_quality() -> str:
    keys = list(QUALITY_PRESETS)
    print("Choose MP3 quality:")
    for i, key in enumerate(keys, start=1):
        print(f"  {i}) {QUALITY_LABELS[key]}")
    while True:
        raw = input(f"Selection [1-{len(keys)}, default 1]: ").strip()
        if not raw:
            return keys[0]
        if raw.isdigit() and 1 <= int(raw) <= len(keys):
            return keys[int(raw) - 1]
        print("Invalid selection.")


def confirm(prompt: str) -> bool:
    return input(f"{prompt} [y/N]: ").strip().lower() in ("y", "yes")


def copy_tags(src: Path, dst: Path) -> None:
    """Copy every FLAC Vorbis comment onto the MP3 as an ID3v2.3 frame,
    mapping well-known keys and preserving everything else (including
    acoustic/fingerprint tags like ACOUSTID_*) as TXXX frames."""
    flac_tags = FLAC(src)
    id3 = ID3()
    for key, values in flac_tags.items():
        joined = "; ".join(values)
        builder = _STANDARD_FRAME_BUILDERS.get(key.lower())
        frame = builder(joined) if builder else TXXX(encoding=3, desc=key, text=joined)
        id3.add(frame)
    id3.save(dst, v2_version=3)


async def run_ffmpeg(src: Path, dst: Path, quality_args: list[str], log: TextIO) -> bool:
    cmd = [
        "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
        "-i", str(src), "-map_metadata", "-1", *quality_args, str(dst),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    output = await proc.communicate()
    if output[0]:
        log.write(output[0].decode(errors="replace"))
    return proc.returncode == 0


async def convert_one(src: Path, quality_args: list[str], log: TextIO) -> ConversionResult:
    dst = src.with_suffix(".mp3")
    ok = await run_ffmpeg(src, dst, quality_args, log)
    ok = ok and dst.is_file() and dst.stat().st_size > 0

    if ok:
        try:
            copy_tags(src, dst)
        except Exception as exc:  # noqa: BLE001 - any tag failure means "not done"
            ok = False
            log.write(f"tag copy failed for {src}: {exc}\n")

    if ok:
        src.unlink()
        return ConversionResult(src, True)

    dst.unlink(missing_ok=True)
    log.write(f"FAILED: {src}\n")
    return ConversionResult(src, False, "conversion failed, see log")


async def convert_all(
    files: list[Path], quality: str, log_path: Path, workers: int
) -> list[ConversionResult]:
    quality_args = QUALITY_PRESETS[quality]
    semaphore = asyncio.Semaphore(workers)
    results: list[ConversionResult] = []
    total = len(files)

    with log_path.open("a", encoding="utf-8") as log:

        async def worker(path: Path) -> None:
            async with semaphore:
                result = await convert_one(path, quality_args, log)
            results.append(result)
            status = "OK" if result.ok else "FAIL"
            print(f"\r[{len(results)}/{total}] {status}: {path.name}" + " " * 10, end="", flush=True)

        await asyncio.gather(*(worker(f) for f in files))

    print()
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recursively transcode FLAC files to MP3.")
    parser.add_argument("folder", nargs="?", type=Path, help="Root folder to scan (prompted if omitted)")
    parser.add_argument("--quality", choices=QUALITY_PRESETS, help="MP3 quality preset (prompted if omitted)")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    parser.add_argument(
        "--workers", type=int, default=min(4, os.cpu_count() or 1), help="Parallel ffmpeg jobs"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    check_dependencies()

    if args.folder is not None:
        if not args.folder.is_dir():
            sys.exit(f"error: not a directory: {args.folder}")
        root = args.folder
    else:
        root = prompt_root_folder(Path.cwd())

    files = find_flac_files(root)
    if not files:
        print(f"No .flac files found under: {root}")
        return

    quality = args.quality or prompt_quality()

    print(f"\nRoot folder: {root}")
    print(f"FLAC files found: {len(files)}")
    print(f"Quality: {QUALITY_LABELS[quality]}")
    print("Each file is converted to .mp3 in place; the original .flac is deleted")
    print("only after the .mp3 is verified.\n")

    if not args.yes and not confirm("Proceed?"):
        return

    log_path = root / f"flac2mp3-{datetime.now():%Y%m%d-%H%M%S}.log"
    results = asyncio.run(convert_all(files, quality, log_path, args.workers))

    ok_count = sum(r.ok for r in results)
    fail_count = len(results) - ok_count
    print(f"\nConverted: {ok_count}")
    print(f"Failed: {fail_count}")
    if fail_count:
        print(f"See log: {log_path}")


if __name__ == "__main__":
    main()
