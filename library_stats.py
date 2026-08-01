"""Scans a folder tree and tallies release types (Album, EP, Single,
Compilation, Promo, etc.) across a music collection.

Reads the same tag this tool already round-trips when converting FLAC to
MP3: the "releasetype" Vorbis comment (FLAC) / "MusicBrainz Album Type"
TXXX frame (MP3, as core.copy_tags writes it) - a MusicBrainz release-type
value like "album", "ep", "single", "compilation", "soundtrack", etc.
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3

_AUDIO_EXTENSIONS = {".flac", ".mp3"}
UNKNOWN_LABEL = "Unknown"


def _release_type_for_file(path: Path) -> str | None:
    """Best-effort: read the MusicBrainz release type tag from one file.
    Returns None if unreadable or untagged - a single bad file must never
    abort the whole scan."""
    try:
        suffix = path.suffix.lower()
        if suffix == ".flac":
            values = FLAC(path).get("releasetype")
            return values[0] if values else None
        if suffix == ".mp3":
            frame = ID3(path).get("TXXX:MusicBrainz Album Type")
            return frame.text[0] if frame and frame.text else None
    except Exception:  # noqa: BLE001 - diagnostic scan, never fatal
        return None
    return None


def scan_release_types(root: Path) -> Counter[str]:
    """Walk `root` recursively. Every directory that directly contains at
    least one audio file is treated as one release (album/single/EP/...);
    its type is read from a single representative file in that directory,
    since release type is an album-level property shared by all its
    tracks. A release with no readable/tagged type is counted as
    "Unknown". Returns a Counter mapping title-cased type name to release
    count."""
    counts: Counter[str] = Counter()
    for dirpath, _dirnames, filenames in os.walk(root):
        audio_files = sorted(f for f in filenames if Path(f).suffix.lower() in _AUDIO_EXTENSIONS)
        if not audio_files:
            continue
        release_type = _release_type_for_file(Path(dirpath) / audio_files[0])
        label = release_type.strip().title() if release_type and release_type.strip() else UNKNOWN_LABEL
        counts[label] += 1
    return counts
