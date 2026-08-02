"""Scans a folder tree and tallies release types (Album, EP, Single,
Compilation, Promo, etc.) and release provenance (Original, Reissue,
Compilation, Unknown) across a music collection, and can sort reissues
into a "Reissues" subfolder of their own artist folder.

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
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3

_AUDIO_EXTENSIONS = {".flac", ".mp3"}
UNKNOWN_LABEL = "Unknown"
REISSUES_FOLDER_NAME = "Reissues"
COMPILATIONS_FOLDER_NAME = "Compilations"
# Which classify_provenance() label sorts into which subfolder - the two
# categories that clutter an artist folder with releases that aren't the
# original studio album (a compilation pulls the same songs together from
# across many albums; a reissue is the same album re-dated).
_DECLUTTER_FOLDERS = {"Reissue": REISSUES_FOLDER_NAME, "Compilation": COMPILATIONS_FOLDER_NAME}


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


@dataclass
class DeclutterMove:
    """One planned relocation: move `source` (a release directory
    classified as a Reissue or Compilation) to `destination` (a
    "Reissues"/"Compilations" subfolder of its own parent directory).
    `selected` starts True and is meant to be flipped off by the caller
    (e.g. a "Keep as it is" toggle in the UI) before execute_declutter_moves
    runs - unselected moves are left alone. `error` is set by
    execute_declutter_moves: None means success (or not yet attempted)."""

    source: Path
    destination: Path
    selected: bool = True
    error: str | None = None


def _plan_moves(root: Path, wanted_label: str | None) -> list[DeclutterMove]:
    """Shared walk for plan_reissue_moves/plan_compilation_moves/
    plan_declutter_moves: one release per directory (see
    scan_release_types), skipping anything already sorted into a
    Reissues/Compilations subfolder. `wanted_label` restricts to just
    "Reissue" or "Compilation"; None plans both in a single walk."""
    moves = []
    for dirpath, filename in _iter_releases(root):
        if dirpath.parent.name in (REISSUES_FOLDER_NAME, COMPILATIONS_FOLDER_NAME):
            continue
        release_types, date, originaldate = _read_release_tags(dirpath / filename)
        label = classify_provenance(release_types, date, originaldate)
        if wanted_label is not None and label != wanted_label:
            continue
        folder_name = _DECLUTTER_FOLDERS.get(label)
        if folder_name is None:
            continue
        moves.append(DeclutterMove(dirpath, dirpath.parent / folder_name / dirpath.name))
    return moves


def plan_reissue_moves(root: Path) -> list[DeclutterMove]:
    """Walk `root` recursively and plan moving every release classified as
    a Reissue into a "Reissues" subfolder of its own parent directory -
    e.g. "Simple Minds/Album (1998 Remaster)" -> "Simple Minds/Reissues/
    Album (1998 Remaster)". Read-only: does not touch the filesystem."""
    return _plan_moves(root, "Reissue")


def plan_compilation_moves(root: Path) -> list[DeclutterMove]:
    """Walk `root` recursively and plan moving every release classified as
    a Compilation into a "Compilations" subfolder of its own parent
    directory - e.g. "Simple Minds/Greatest Hits" -> "Simple Minds/
    Compilations/Greatest Hits". Read-only: does not touch the
    filesystem."""
    return _plan_moves(root, "Compilation")


def plan_declutter_moves(root: Path) -> list[DeclutterMove]:
    """Walk `root` recursively once and plan moving every release
    classified as a Reissue or a Compilation into a "Reissues"/
    "Compilations" subfolder of its own parent directory - the combination
    of plan_reissue_moves and plan_compilation_moves, minus a second walk.
    Read-only: does not touch the filesystem."""
    return _plan_moves(root, None)


def execute_declutter_moves(moves: list[DeclutterMove]) -> None:
    """Perform every `selected` move from a plan_*_moves() call, in place:
    each DeclutterMove's `error` is set to None on success or a message on
    failure (a conflicting destination, a permissions error, ...), so one
    bad move never aborts the rest of the batch."""
    for move in moves:
        if not move.selected:
            continue
        if move.destination.exists():
            move.error = f"destination already exists: {move.destination}"
            continue
        try:
            move.destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(move.source), str(move.destination))
            move.error = None
        except OSError as exc:
            move.error = str(exc)
