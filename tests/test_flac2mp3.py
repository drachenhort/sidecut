import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest
from mutagen.id3 import ID3
from mutagen.mp3 import MP3

import flac2mp3

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def make_flac(path: Path, **tags: str) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
    for key, value in tags.items():
        cmd += ["-metadata", f"{key}={value}"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)


def test_find_flac_files_recursive_case_insensitive(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.flac").touch()
    (tmp_path / "a" / "two.FLAC").touch()
    (tmp_path / "a" / "not_flac.mp3").touch()

    found = flac2mp3.find_flac_files(tmp_path)

    assert [p.name for p in found] == ["one.flac", "two.FLAC"]


def test_convert_one_preserves_standard_and_custom_tags(tmp_path: Path) -> None:
    src = tmp_path / "song.flac"
    make_flac(
        src,
        artist="Test Artist",
        album="Test Album",
        title="Test Title",
        ACOUSTID_ID="abcd-1234",
        ACOUSTID_FINGERPRINT="AQADfake",
        MUSICBRAINZ_TRACKID="mb-track-123",
    )

    result = asyncio.run(flac2mp3.convert_one(src, flac2mp3.QUALITY_PRESETS["v0"], open("/dev/null", "w")))

    assert result.ok
    assert not src.exists()
    dst = tmp_path / "song.mp3"
    assert dst.is_file()

    id3 = ID3(dst)
    assert id3["TPE1"].text == ["Test Artist"]
    assert id3["TALB"].text == ["Test Album"]
    assert id3["TIT2"].text == ["Test Title"]

    # ffmpeg lowercases Vorbis comment keys set via -metadata; copy_tags
    # preserves whatever case is present in the source file's tags.
    txxx_descs = {frame.desc: frame.text[0] for frame in id3.getall("TXXX")}
    assert txxx_descs["acoustid_id"] == "abcd-1234"
    assert txxx_descs["acoustid_fingerprint"] == "AQADfake"
    assert txxx_descs["musicbrainz_trackid"] == "mb-track-123"

    assert MP3(dst).info.length > 0


def test_convert_one_keeps_source_on_failure(tmp_path: Path) -> None:
    src = tmp_path / "broken.flac"
    src.write_text("not a real flac file")

    result = asyncio.run(flac2mp3.convert_one(src, flac2mp3.QUALITY_PRESETS["v0"], open("/dev/null", "w")))

    assert not result.ok
    assert src.exists()
    assert not (tmp_path / "broken.mp3").exists()


def test_convert_all_is_idempotent_on_rerun(tmp_path: Path) -> None:
    src = tmp_path / "song.flac"
    make_flac(src, artist="Test Artist")
    log_path = tmp_path / "log.txt"

    first = asyncio.run(flac2mp3.convert_all([src], "v0", log_path, workers=2))
    assert first[0].ok

    remaining = flac2mp3.find_flac_files(tmp_path)
    assert remaining == []
    assert (tmp_path / "song.mp3").is_file()
