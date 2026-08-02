"""Scans a folder tree and tallies release types (Album, EP, Single,
Compilation, Promo, etc.) and release provenance (Original, Reissue,
Compilation, Unknown) across a music collection.

Reads the same tags this tool already round-trips when converting FLAC to
MP3: the "releasetype" Vorbis comment (FLAC) / "MusicBrainz Album Type"
TXXX frame (MP3, as core.copy_tags writes it) - a MusicBrainz release-type
value like "album", "ep", "single", "compilation", "soundtrack", etc. -
plus "date"/"originaldate" (FLAC) / TDRC/TDOR (MP3), which Picard sets to
this specific release's date and the release-group's first release date
respectively. A release tagged with the "compilation" secondary type is a
compilation; one whose release date differs from its original date is a
reissue; anything else with a release-type tag is treated as an original
release.
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3

_AUDIO_EXTENSIONS = {".flac", ".mp3"}
UNKNOWN_LABEL = "Unknown"


def _read_release_tags(path: Path) -> tuple[list[str], str | None, str | None]:
    """Best-effort: read the release-type, date, and original-date tags
    from one file. Returns ([], None, None) if unreadable or untagged - a
    single bad file must never abort the whole scan."""
    try:
        suffix = path.suffix.lower()
        if suffix == ".flac":
            tags = FLAC(path)
            release_types = list(tags.get("releasetype") or [])
            date = (tags.get("date") or [None])[0]
            originaldate = (tags.get("originaldate") or [None])[0]
            return release_types, date, originaldate
        if suffix == ".mp3":
            id3 = ID3(path)
            type_frame = id3.get("TXXX:MusicBrainz Album Type")
            release_types = type_frame.text[0].split("; ") if type_frame and type_frame.text else []
            date_frame = id3.get("TDRC")
            originaldate_frame = id3.get("TDOR")
            date = str(date_frame.text[0]) if date_frame and date_frame.text else None
            originaldate = str(originaldate_frame.text[0]) if originaldate_frame and originaldate_frame.text else None
            return release_types, date, originaldate
    except Exception:  # noqa: BLE001 - diagnostic scan, never fatal
        return [], None, None
    return [], None, None


def _year(date: str | None) -> str | None:
    """First four characters of a date tag ("2010-04-01" -> "2010"), which
    is as much precision as either "date" or "originaldate" is guaranteed
    to share."""
    return date.strip()[:4] if date and date.strip() else None


def classify_provenance(release_types: list[str], date: str | None, originaldate: str | None) -> str:
    """Best-effort provenance label for one release: "Compilation" if its
    release-type tag includes the MusicBrainz "compilation" secondary
    type, "Reissue" if this release's year differs from the release
    group's original release year, "Original" if tagged but neither of
    the above, else "Unknown"."""
    normalized_types = {t.strip().lower() for t in release_types if t.strip()}
    if "compilation" in normalized_types:
        return "Compilation"
    original_year = _year(originaldate)
    release_year = _year(date)
    if original_year and release_year and original_year != release_year:
        return "Reissue"
    if normalized_types or date or originaldate:
        return "Original"
    return UNKNOWN_LABEL


def _iter_releases(root: Path) -> list[tuple[Path, str]]:
    """Every directory under `root` that directly contains at least one
    audio file, paired with a single representative file from it - one
    release per directory (album/single/EP/...), since release-level tags
    are shared by all of a release's tracks."""
    releases = []
    for dirpath, _dirnames, filenames in os.walk(root):
        audio_files = sorted(f for f in filenames if Path(f).suffix.lower() in _AUDIO_EXTENSIONS)
        if audio_files:
            releases.append((Path(dirpath), audio_files[0]))
    return releases


def scan_release_types(root: Path) -> Counter[str]:
    """Walk `root` recursively. Every directory that directly contains at
    least one audio file is treated as one release (album/single/EP/...);
    its type is read from a single representative file in that directory,
    since release type is an album-level property shared by all its
    tracks. A release with no readable/tagged type is counted as
    "Unknown". Returns a Counter mapping title-cased type name to release
    count."""
    counts: Counter[str] = Counter()
    for dirpath, filename in _iter_releases(root):
        release_types, _date, _originaldate = _read_release_tags(dirpath / filename)
        release_type = release_types[0] if release_types else None
        label = release_type.strip().title() if release_type and release_type.strip() else UNKNOWN_LABEL
        counts[label] += 1
    return counts


def scan_release_provenance(root: Path) -> Counter[str]:
    """Walk `root` recursively, one release per directory (see
    scan_release_types), and classify each as Compilation, Reissue,
    Original, or Unknown using classify_provenance. Returns a Counter
    mapping label to release count."""
    counts: Counter[str] = Counter()
    for dirpath, filename in _iter_releases(root):
        release_types, date, originaldate = _read_release_tags(dirpath / filename)
        counts[classify_provenance(release_types, date, originaldate)] += 1
    return counts
