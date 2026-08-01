import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3
from mutagen.mp3 import MP3

import core

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

    found = core.find_flac_files(tmp_path)

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

    with open("/dev/null", "w") as log:
        result = core.convert_one(src, core.QUALITY_PRESETS["v0"], log)

    assert result.ok
    assert not src.exists()
    dst = tmp_path / "song.mp3"
    assert dst.is_file()

    id3 = ID3(dst)
    assert id3["TPE1"].text == ["Test Artist"]
    assert id3["TALB"].text == ["Test Album"]
    assert id3["TIT2"].text == ["Test Title"]

    # AcoustID/MusicBrainz tags are written under Picard's TXXX descriptions
    # (not the raw Vorbis comment key) so tagged files round-trip through
    # Picard identically; the recording ID is a UFID frame, not TXXX.
    txxx_descs = {frame.desc: frame.text[0] for frame in id3.getall("TXXX")}
    assert txxx_descs["Acoustid Id"] == "abcd-1234"
    assert txxx_descs["Acoustid Fingerprint"] == "AQADfake"

    ufid = id3.getall("UFID:http://musicbrainz.org")[0]
    assert ufid.data == b"mb-track-123"

    assert MP3(dst).info.length > 0


def test_convert_one_preserves_embedded_cover_art(tmp_path: Path) -> None:
    src = tmp_path / "song.flac"
    make_flac(src)

    cover_bytes = b"\x89PNG\r\n\x1a\nfake-but-good-enough-for-this-test"
    picture = Picture()
    picture.type = 3  # front cover
    picture.mime = "image/png"
    picture.desc = "cover"
    picture.data = cover_bytes
    flac_file = FLAC(src)
    flac_file.add_picture(picture)
    flac_file.save()

    with open("/dev/null", "w") as log:
        result = core.convert_one(src, core.QUALITY_PRESETS["v0"], log)

    assert result.ok
    id3 = ID3(tmp_path / "song.mp3")
    apics = id3.getall("APIC")
    assert len(apics) == 1
    assert apics[0].data == cover_bytes
    assert apics[0].mime == "image/png"
    assert apics[0].type == 3


def test_convert_one_reports_progress(tmp_path: Path) -> None:
    src = tmp_path / "song.flac"
    make_flac(src)
    updates: list[float] = []

    with open("/dev/null", "w") as log:
        result = core.convert_one(src, core.QUALITY_PRESETS["v0"], log, on_progress=lambda p: updates.append(p.percent))

    assert result.ok
    assert updates
    assert updates[-1] >= 90.0


def test_convert_one_keeps_source_on_failure(tmp_path: Path) -> None:
    src = tmp_path / "broken.flac"
    src.write_text("not a real flac file")

    with open("/dev/null", "w") as log:
        result = core.convert_one(src, core.QUALITY_PRESETS["v0"], log)

    assert not result.ok
    assert src.exists()
    assert not (tmp_path / "broken.mp3").exists()


def test_convert_one_preserves_both_date_and_year(tmp_path: Path) -> None:
    src = tmp_path / "song.flac"
    make_flac(src, date="2019-05-01", year="1999")

    with open("/dev/null", "w") as log:
        result = core.convert_one(src, core.QUALITY_PRESETS["v0"], log)

    assert result.ok
    id3 = ID3(tmp_path / "song.mp3")
    assert str(id3["TDRC"].text[0]) == "2019-05-01"
    txxx_descs = {frame.desc: frame.text[0] for frame in id3.getall("TXXX")}
    assert txxx_descs["year"] == "1999"


def test_convert_one_reports_failure_instead_of_raising_on_missing_source(tmp_path: Path) -> None:
    src = tmp_path / "gone.flac"  # never created, e.g. deleted after the scan

    with open("/dev/null", "w") as log:
        result = core.convert_one(src, core.QUALITY_PRESETS["v0"], log)

    assert not result.ok
    assert not (tmp_path / "gone.mp3").exists()


def _fake_fpcalc_run(*args, **kwargs) -> Mock:
    return Mock(stdout=json.dumps({"duration": 180, "fingerprint": "AQADfake"}))


def test_check_acoustid_reports_match_for_tagged_recording(tmp_path: Path) -> None:
    src = tmp_path / "song.flac"
    make_flac(src, MUSICBRAINZ_TRACKID="mb-track-123")
    response = Mock()
    response.json.return_value = {
        "status": "ok",
        "results": [{"id": "acoustid-1", "score": 0.95, "recordings": [{"id": "mb-track-123", "title": "Song"}]}],
    }

    with patch("subprocess.run", side_effect=_fake_fpcalc_run), patch("requests.get", return_value=response):
        result = core.check_acoustid(src, "fake-api-key")

    assert result.status == "match"
    assert result.recording_id == "mb-track-123"


def test_check_acoustid_reports_mismatch_when_tag_disagrees(tmp_path: Path) -> None:
    src = tmp_path / "song.flac"
    make_flac(src, MUSICBRAINZ_TRACKID="mb-track-wrong")
    response = Mock()
    response.json.return_value = {
        "status": "ok",
        "results": [
            {
                "id": "acoustid-1",
                "score": 0.95,
                "recordings": [{"id": "mb-track-correct", "title": "Song", "artists": [{"name": "Artist"}]}],
            }
        ],
    }

    with patch("subprocess.run", side_effect=_fake_fpcalc_run), patch("requests.get", return_value=response):
        result = core.check_acoustid(src, "fake-api-key")

    assert result.status == "mismatch"
    assert result.recording_id == "mb-track-correct"


def test_check_acoustid_reports_identified_when_untagged(tmp_path: Path) -> None:
    src = tmp_path / "song.flac"
    make_flac(src)
    response = Mock()
    response.json.return_value = {
        "status": "ok",
        "results": [{"id": "acoustid-1", "score": 0.9, "recordings": [{"id": "mb-track-1", "title": "Song"}]}],
    }

    with patch("subprocess.run", side_effect=_fake_fpcalc_run), patch("requests.get", return_value=response):
        result = core.check_acoustid(src, "fake-api-key")

    assert result.status == "identified"
    assert result.recording_id == "mb-track-1"


def test_check_acoustid_reports_no_match(tmp_path: Path) -> None:
    src = tmp_path / "song.flac"
    make_flac(src)
    response = Mock()
    response.json.return_value = {"status": "ok", "results": []}

    with patch("subprocess.run", side_effect=_fake_fpcalc_run), patch("requests.get", return_value=response):
        result = core.check_acoustid(src, "fake-api-key")

    assert result.status == "no_match"


def test_check_acoustid_reports_error_on_missing_fpcalc(tmp_path: Path) -> None:
    src = tmp_path / "song.flac"
    make_flac(src)

    with patch("subprocess.run", side_effect=FileNotFoundError("fpcalc not found")):
        result = core.check_acoustid(src, "fake-api-key")

    assert result.status == "error"


def test_check_acoustid_reports_error_on_request_failure(tmp_path: Path) -> None:
    src = tmp_path / "song.flac"
    make_flac(src)

    with (
        patch("subprocess.run", side_effect=_fake_fpcalc_run),
        patch("requests.get", side_effect=requests.ConnectionError("no network")),
    ):
        result = core.check_acoustid(src, "fake-api-key")

    assert result.status == "error"


def test_convert_one_respects_cancellation(tmp_path: Path) -> None:
    src = tmp_path / "song.flac"
    make_flac(src)

    with open("/dev/null", "w") as log:
        result = core.convert_one(src, core.QUALITY_PRESETS["v0"], log, should_cancel=lambda: True)

    assert not result.ok
    assert src.exists()
