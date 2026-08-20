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


@pytest.mark.parametrize("disc_names", [["CD 01", "CD 02"], ["Disc 1", "Disc 2", "Disc 3"], ["CD1"]])
def test_multi_disc_release_counts_as_one_release(tmp_path: Path, disc_names: list[str]) -> None:
    album = tmp_path / "Artist" / "Box Set (2016)"
    for disc in disc_names:
        (album / disc).mkdir(parents=True)
        make_flac(album / disc / "01.flac", releasetype="album", date="2016-01-01", originaldate="1982-01-01")

    counts = library_stats.scan_release_provenance(tmp_path)

    assert counts == {"Reissue": 1}


def test_multi_disc_release_moves_whole_album_folder_not_each_disc(tmp_path: Path) -> None:
    album = tmp_path / "Artist" / "Box Set (2016)"
    for disc in ("CD 01", "CD 02"):
        (album / disc).mkdir(parents=True)
        make_flac(album / disc / "01.flac", releasetype="album", date="2016-01-01", originaldate="1982-01-01")

    moves = library_stats.plan_reissue_moves(tmp_path)

    assert len(moves) == 1
    assert moves[0].source == album
    assert moves[0].destination == tmp_path / "Artist" / "Reissues" / "Box Set (2016)"


def test_ordinary_artist_folder_with_multiple_albums_is_not_collapsed(tmp_path: Path) -> None:
    # Subfolders that aren't disc-named (e.g. real album titles) must NOT
    # be rolled up into a single "release" - only "CD N"/"Disc N" folders
    # should trigger the multi-disc collapse.
    album1 = tmp_path / "Artist" / "Album One (2020)"
    album1.mkdir(parents=True)
    make_flac(album1 / "01.flac", releasetype="album")

    album2 = tmp_path / "Artist" / "Some EP (2021)"
    album2.mkdir(parents=True)
    make_flac(album2 / "01.flac", releasetype="ep")

    counts = library_stats.scan_release_types(tmp_path)

    assert counts == {"Album": 1, "Ep": 1}


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


def test_plan_reissue_moves_targets_reissues_subfolder_of_parent(tmp_path: Path) -> None:
    reissue = tmp_path / "Simple Minds" / "Album (1998 Remaster)"
    reissue.mkdir(parents=True)
    make_flac(reissue / "01.flac", releasetype="album", date="1998-01-01", originaldate="1980-01-01")

    original = tmp_path / "Simple Minds" / "Debut (1980)"
    original.mkdir(parents=True)
    make_flac(original / "01.flac", releasetype="album", date="1980-01-01", originaldate="1980-01-01")

    moves = library_stats.plan_reissue_moves(tmp_path)

    assert len(moves) == 1
    assert moves[0].source == reissue
    assert moves[0].destination == tmp_path / "Simple Minds" / "Reissues" / "Album (1998 Remaster)"
    assert moves[0].selected is True
    assert moves[0].error is None


def test_plan_reissue_moves_skips_releases_already_under_reissues_folder(tmp_path: Path) -> None:
    already_sorted = tmp_path / "Simple Minds" / "Reissues" / "Album (1998 Remaster)"
    already_sorted.mkdir(parents=True)
    make_flac(already_sorted / "01.flac", releasetype="album", date="1998-01-01", originaldate="1980-01-01")

    moves = library_stats.plan_reissue_moves(tmp_path)

    assert moves == []


def test_execute_declutter_moves_moves_the_directory(tmp_path: Path) -> None:
    reissue = tmp_path / "Simple Minds" / "Album (1998 Remaster)"
    reissue.mkdir(parents=True)
    make_flac(reissue / "01.flac", releasetype="album", date="1998-01-01", originaldate="1980-01-01")

    moves = library_stats.plan_reissue_moves(tmp_path)
    library_stats.execute_declutter_moves(moves)

    assert moves[0].error is None
    assert not reissue.exists()
    destination = tmp_path / "Simple Minds" / "Reissues" / "Album (1998 Remaster)"
    assert destination.is_dir()
    assert (destination / "01.flac").exists()


def test_execute_declutter_moves_skips_unselected_moves(tmp_path: Path) -> None:
    reissue = tmp_path / "Simple Minds" / "Album (1998 Remaster)"
    reissue.mkdir(parents=True)
    make_flac(reissue / "01.flac", releasetype="album", date="1998-01-01", originaldate="1980-01-01")

    moves = library_stats.plan_reissue_moves(tmp_path)
    moves[0].selected = False
    library_stats.execute_declutter_moves(moves)

    assert moves[0].error is None
    assert reissue.exists()
    assert not (tmp_path / "Simple Minds" / "Reissues").exists()


def test_execute_declutter_moves_reports_error_without_aborting_batch(tmp_path: Path) -> None:
    reissue = tmp_path / "Simple Minds" / "Album (1998 Remaster)"
    reissue.mkdir(parents=True)
    make_flac(reissue / "01.flac", releasetype="album", date="1998-01-01", originaldate="1980-01-01")
    conflicting_destination = tmp_path / "Simple Minds" / "Reissues" / "Album (1998 Remaster)"
    conflicting_destination.mkdir(parents=True)  # already occupied

    moves = library_stats.plan_reissue_moves(tmp_path)
    library_stats.execute_declutter_moves(moves)

    assert moves[0].error is not None
    assert reissue.exists()  # left in place, not overwritten


def test_plan_compilation_moves_targets_compilations_subfolder_of_parent(tmp_path: Path) -> None:
    compilation = tmp_path / "Simple Minds" / "Greatest Hits"
    compilation.mkdir(parents=True)
    path = compilation / "01.flac"
    make_flac(path, date="1999-01-01")
    flac = FLAC(path)
    flac["releasetype"] = ["album", "compilation"]
    flac.save()

    original = tmp_path / "Simple Minds" / "Debut (1980)"
    original.mkdir(parents=True)
    make_flac(original / "01.flac", releasetype="album", date="1980-01-01", originaldate="1980-01-01")

    moves = library_stats.plan_compilation_moves(tmp_path)

    assert len(moves) == 1
    assert moves[0].source == compilation
    assert moves[0].destination == tmp_path / "Simple Minds" / "Compilations" / "Greatest Hits"


def test_plan_declutter_moves_combines_reissues_and_compilations_in_one_walk(tmp_path: Path) -> None:
    reissue = tmp_path / "Simple Minds" / "Album (1998 Remaster)"
    reissue.mkdir(parents=True)
    make_flac(reissue / "01.flac", releasetype="album", date="1998-01-01", originaldate="1980-01-01")

    compilation = tmp_path / "Simple Minds" / "Greatest Hits"
    compilation.mkdir(parents=True)
    path = compilation / "01.flac"
    make_flac(path, date="1999-01-01")
    flac = FLAC(path)
    flac["releasetype"] = ["album", "compilation"]
    flac.save()

    original = tmp_path / "Simple Minds" / "Debut (1980)"
    original.mkdir(parents=True)
    make_flac(original / "01.flac", releasetype="album", date="1980-01-01", originaldate="1980-01-01")

    moves = library_stats.plan_declutter_moves(tmp_path)

    destinations = {move.source.name: move.destination for move in moves}
    assert destinations == {
        "Album (1998 Remaster)": tmp_path / "Simple Minds" / "Reissues" / "Album (1998 Remaster)",
        "Greatest Hits": tmp_path / "Simple Minds" / "Compilations" / "Greatest Hits",
    }


def test_scan_missing_tags_counts_complete_and_incomplete(tmp_path: Path) -> None:
    complete = tmp_path / "complete.flac"
    make_flac(complete, title="Song", artist="Artist", album="Album", track="1", date="2020-01-01")

    incomplete = tmp_path / "incomplete.flac"
    make_flac(incomplete, title="Song 2")  # missing artist/album/tracknumber/date

    report = library_stats.scan_missing_tags(tmp_path)

    assert report.total_files == 2
    assert report.complete_count == 1
    assert report.incomplete_count == 1
    assert report.incomplete_files == [incomplete]
    assert report.missing_by_tag == {"artist": 1, "album": 1, "tracknumber": 1, "date": 1}


def test_scan_missing_tags_reads_mp3_id3_frames(tmp_path: Path) -> None:
    flac_src = tmp_path / "song.flac"
    make_flac(flac_src, title="Song", artist="Artist", album="Album", track="1", date="2020-01-01")
    with open("/dev/null", "w") as log:
        result = core.convert_one(flac_src, core.QUALITY_PRESETS["v0"], log)
    assert result.ok

    report = library_stats.scan_missing_tags(tmp_path)

    assert report.total_files == 1  # convert_one deletes the source .flac on success
    assert report.incomplete_files == []
    assert report.complete_count == 1


def test_scan_missing_tags_only_lists_incomplete_files(tmp_path: Path) -> None:
    complete = tmp_path / "complete.flac"
    make_flac(complete, title="Song", artist="Artist", album="Album", track="1", date="2020-01-01")
    other_complete = tmp_path / "complete2.flac"
    make_flac(other_complete, title="Song 2", artist="Artist", album="Album", track="2", date="2020-01-01")

    report = library_stats.scan_missing_tags(tmp_path)

    assert report.incomplete_files == []
    assert report.complete_count == 2


def test_plan_declutter_moves_skips_releases_already_sorted(tmp_path: Path) -> None:
    already_sorted_reissue = tmp_path / "Simple Minds" / "Reissues" / "Album (1998 Remaster)"
    already_sorted_reissue.mkdir(parents=True)
    make_flac(already_sorted_reissue / "01.flac", releasetype="album", date="1998-01-01", originaldate="1980-01-01")

    already_sorted_compilation = tmp_path / "Simple Minds" / "Compilations" / "Greatest Hits"
    already_sorted_compilation.mkdir(parents=True)
    path = already_sorted_compilation / "01.flac"
    make_flac(path, date="1999-01-01")
    flac = FLAC(path)
    flac["releasetype"] = ["album", "compilation"]
    flac.save()

    assert library_stats.plan_declutter_moves(tmp_path) == []
