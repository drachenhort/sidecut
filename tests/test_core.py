import json
import shutil
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3
from mutagen.mp3 import MP3

import core

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(autouse=True)
def _fast_acoustid_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # The real limiter is a module-level singleton shared across every
    # check_acoustid() call so it can enforce AcoustID's global rate limit;
    # swap in an effectively-unlimited one so tests don't pay for real sleeps.
    monkeypatch.setattr(core, "_acoustid_rate_limiter", core._RateLimiter(1_000_000))


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


def test_rate_limiter_spaces_out_calls() -> None:
    limiter = core._RateLimiter(per_second=20)  # 50ms apart
    start = time.monotonic()
    for _ in range(3):
        limiter.wait()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.1  # 2 gaps of >= 50ms between 3 calls


def test_rate_limiter_shared_across_threads_caps_total_rate() -> None:
    limiter = core._RateLimiter(per_second=20)  # 50ms apart
    start = time.monotonic()
    threads = [threading.Thread(target=limiter.wait) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.25  # 5 gaps of >= 50ms between 6 calls, however interleaved


def test_check_acoustid_uses_the_shared_rate_limiter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "song.flac"
    make_flac(src)
    response = Mock()
    response.json.return_value = {"status": "ok", "results": []}
    waited = []
    monkeypatch.setattr(core._acoustid_rate_limiter, "wait", lambda: waited.append(True))

    with patch("subprocess.run", side_effect=_fake_fpcalc_run), patch("requests.get", return_value=response):
        core.check_acoustid(src, "fake-api-key")

    assert waited


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
    make_flac(src, artist="Wrong Artist", title="Wrong Title", MUSICBRAINZ_TRACKID="mb-track-wrong")
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
    # The correct match (artist, title, and MBID) must be surfaced so the
    # user knows what to retag it to, not just that it's wrong.
    assert "Wrong Artist - Wrong Title" in result.detail
    assert "mb-track-wrong" in result.detail
    assert "Artist - Song" in result.detail
    assert "mb-track-correct" in result.detail


def test_check_acoustid_reports_mismatch_with_no_linked_recording(tmp_path: Path) -> None:
    src = tmp_path / "song.flac"
    make_flac(src, artist="Wrong Artist", title="Wrong Title", MUSICBRAINZ_TRACKID="mb-track-wrong")
    response = Mock()
    response.json.return_value = {
        "status": "ok",
        "results": [{"id": "acoustid-1", "score": 0.6, "recordings": []}],
    }

    with patch("subprocess.run", side_effect=_fake_fpcalc_run), patch("requests.get", return_value=response):
        result = core.check_acoustid(src, "fake-api-key")

    assert result.status == "mismatch"
    assert result.recording_id is None
    assert "Wrong Artist - Wrong Title" in result.detail


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


def test_check_acoustid_surfaces_api_error_message_instead_of_raising(tmp_path: Path) -> None:
    # AcoustID returns a JSON body with the real reason (e.g. a bad API key)
    # even on a 400 response; that message must reach the caller instead of
    # a generic "400 Client Error" from raise_for_status().
    src = tmp_path / "song.flac"
    make_flac(src)
    response = Mock()
    response.json.return_value = {"status": "error", "error": {"code": 4, "message": "invalid API key"}}
    response.raise_for_status.side_effect = AssertionError("should not be called when the body parses as JSON")

    with patch("subprocess.run", side_effect=_fake_fpcalc_run), patch("requests.get", return_value=response):
        result = core.check_acoustid(src, "bogus-key")

    assert result.status == "error"
    assert result.detail == "AcoustID error: invalid API key"


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
