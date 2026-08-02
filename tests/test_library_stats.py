import shutil
import subprocess
from pathlib import Path

import pytest
from mutagen.flac import FLAC
from mutagen.id3 import ID3

import core
import library_stats

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def make_flac(path: Path, **tags: str) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
    for key, value in tags.items():
        cmd += ["-metadata", f"{key}={value}"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)


def test_scan_release_types_counts_one_per_directory(tmp_path: Path) -> None:
    album1 = tmp_path / "Artist" / "Album One (2020)"
    album1.mkdir(parents=True)
    make_flac(album1 / "01.flac", releasetype="album")
    make_flac(album1 / "02.flac", releasetype="album")  # same dir, must count once

    ep = tmp_path / "Artist" / "Some EP (2021)"
    ep.mkdir(parents=True)
    make_flac(ep / "01.flac", releasetype="ep")

    counts = library_stats.scan_release_types(tmp_path)

    assert counts == {"Album": 1, "Ep": 1}


def test_scan_release_types_reads_mp3_txxx_frame(tmp_path: Path) -> None:
    flac_src = tmp_path / "song.flac"
    make_flac(flac_src, releasetype="single")
    with open("/dev/null", "w") as log:
        result = core.convert_one(flac_src, core.QUALITY_PRESETS["v0"], log)
    assert result.ok
    mp3 = tmp_path / "song.mp3"
    assert ID3(mp3).get("TXXX:MusicBrainz Album Type") is not None

    counts = library_stats.scan_release_types(tmp_path)

    assert counts == {"Single": 1}


def test_scan_release_types_labels_untagged_release_as_unknown(tmp_path: Path) -> None:
    album = tmp_path / "Artist" / "Untagged (2019)"
    album.mkdir(parents=True)
    make_flac(album / "01.flac")  # no releasetype tag at all

    counts = library_stats.scan_release_types(tmp_path)

    assert counts == {"Unknown": 1}


def test_scan_release_types_skips_directories_without_audio_files(tmp_path: Path) -> None:
    (tmp_path / "Artist").mkdir()
    (tmp_path / "Artist" / "cover.jpg").touch()
    (tmp_path / "Artist" / "notes.txt").touch()

    counts = library_stats.scan_release_types(tmp_path)

    assert counts == {}


def test_scan_release_types_ignores_unreadable_file_without_crashing(tmp_path: Path) -> None:
    album = tmp_path / "Artist" / "Broken (2019)"
    album.mkdir(parents=True)
    (album / "01.flac").write_text("not a real flac file")

    counts = library_stats.scan_release_types(tmp_path)

    assert counts == {"Unknown": 1}


@pytest.mark.parametrize(
    "release_types,date,originaldate,expected",
    [
        (["album", "compilation"], "1999-01-01", "1999-01-01", "Compilation"),
        (["album"], "2011-06-01", "1980-03-01", "Reissue"),
        (["album"], "1980-03-01", "1980-03-01", "Original"),
        (["album"], None, None, "Original"),
        ([], None, None, "Unknown"),
    ],
)
def test_classify_provenance(
    release_types: list[str], date: str | None, originaldate: str | None, expected: str
) -> None:
    assert library_stats.classify_provenance(release_types, date, originaldate) == expected


def test_scan_release_provenance_detects_reissue_from_date_mismatch(tmp_path: Path) -> None:
    album = tmp_path / "Artist" / "Reissued Album (2011)"
    album.mkdir(parents=True)
    make_flac(album / "01.flac", releasetype="album", date="2011-06-01", originaldate="1980-03-01")

    counts = library_stats.scan_release_provenance(tmp_path)

    assert counts == {"Reissue": 1}


def test_scan_release_provenance_detects_compilation_from_secondary_type(tmp_path: Path) -> None:
    album = tmp_path / "Artist" / "Greatest Hits (1999)"
    album.mkdir(parents=True)
    path = album / "01.flac"
    make_flac(path, date="1999-01-01")
    # Picard writes primary + secondary release types as repeated Vorbis
    # comment fields sharing the "releasetype" key - ffmpeg's -metadata
    # flag can't express that, so append the second value directly.
    flac = FLAC(path)
    flac["releasetype"] = ["album", "compilation"]
    flac.save()

    counts = library_stats.scan_release_provenance(tmp_path)

    assert counts == {"Compilation": 1}


def test_scan_release_provenance_labels_matching_dates_as_original(tmp_path: Path) -> None:
    album = tmp_path / "Artist" / "Debut (1980)"
    album.mkdir(parents=True)
    make_flac(album / "01.flac", releasetype="album", date="1980-03-01", originaldate="1980-03-01")

    counts = library_stats.scan_release_provenance(tmp_path)

    assert counts == {"Original": 1}


def test_scan_release_provenance_labels_untagged_release_as_unknown(tmp_path: Path) -> None:
    album = tmp_path / "Artist" / "Untagged (2019)"
    album.mkdir(parents=True)
    make_flac(album / "01.flac")  # no releasetype/date tags at all

    counts = library_stats.scan_release_provenance(tmp_path)

    assert counts == {"Unknown": 1}
