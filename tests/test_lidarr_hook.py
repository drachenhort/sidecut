import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QSettings

import lidarr
import lidarr_hook

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def make_flac(path: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-metadata", "artist=Test Artist", str(path),
    ]
    subprocess.run(cmd, check=True)


def _settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)


def test_is_invocation_detects_lidarr_eventtype(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("lidarr_eventtype", raising=False)
    assert lidarr_hook.is_invocation() is False
    monkeypatch.setenv("lidarr_eventtype", "Download")
    assert lidarr_hook.is_invocation() is True


def test_run_from_environment_handles_test_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("lidarr_eventtype", "Test")
    assert lidarr_hook.run_from_environment() == 0


def test_run_from_environment_ignores_unsupported_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("lidarr_eventtype", "Grab")
    assert lidarr_hook.run_from_environment() == 0


def test_run_from_environment_errors_without_track_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("lidarr_eventtype", "Download")
    monkeypatch.delenv("lidarr_addedtrackpaths", raising=False)
    monkeypatch.delenv("lidarr_trackfile_path", raising=False)
    assert lidarr_hook.run_from_environment() == 1


def test_run_from_environment_does_nothing_for_non_flac_tracks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("lidarr_eventtype", "Download")
    monkeypatch.setenv("lidarr_addedtrackpaths", str(tmp_path / "already.mp3"))
    assert lidarr_hook.run_from_environment(_settings(tmp_path)) == 0


def test_run_from_environment_converts_and_skips_lidarr_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    flac = tmp_path / "song.flac"
    make_flac(flac)
    monkeypatch.setenv("lidarr_eventtype", "Download")
    monkeypatch.setenv("lidarr_addedtrackpaths", str(flac))
    monkeypatch.setenv("lidarr_artist_path", str(tmp_path))

    with patch("lidarr.import_folder") as import_folder:
        result = lidarr_hook.run_from_environment(_settings(tmp_path))

    assert result == 0
    assert not flac.exists()
    assert (tmp_path / "song.mp3").is_file()
    import_folder.assert_not_called()


def test_run_from_environment_calls_lidarr_import_when_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    flac = tmp_path / "song.flac"
    make_flac(flac)
    monkeypatch.setenv("lidarr_eventtype", "Download")
    monkeypatch.setenv("lidarr_addedtrackpaths", str(flac))
    monkeypatch.setenv("lidarr_artist_path", str(tmp_path))

    settings = _settings(tmp_path)
    settings.setValue("lidarr_url", "http://localhost:8686")
    settings.setValue("lidarr_api_key", "key123")

    with patch("lidarr.import_folder", return_value=(1, 0, [])) as import_folder:
        result = lidarr_hook.run_from_environment(settings)

    assert result == 0
    import_folder.assert_called_once_with("http://localhost:8686", "key123", tmp_path)


def test_run_from_environment_returns_error_on_conversion_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    broken = tmp_path / "broken.flac"
    broken.write_text("not a real flac")
    monkeypatch.setenv("lidarr_eventtype", "Download")
    monkeypatch.setenv("lidarr_addedtrackpaths", str(broken))
    monkeypatch.setenv("lidarr_artist_path", str(tmp_path))

    assert lidarr_hook.run_from_environment(_settings(tmp_path)) == 1


def test_run_from_environment_returns_error_on_lidarr_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    flac = tmp_path / "song.flac"
    make_flac(flac)
    monkeypatch.setenv("lidarr_eventtype", "Download")
    monkeypatch.setenv("lidarr_addedtrackpaths", str(flac))
    monkeypatch.setenv("lidarr_artist_path", str(tmp_path))

    settings = _settings(tmp_path)
    settings.setValue("lidarr_url", "http://localhost:8686")
    settings.setValue("lidarr_api_key", "key123")

    with patch("lidarr.import_folder", side_effect=lidarr.LidarrError("boom")):
        result = lidarr_hook.run_from_environment(settings)

    assert result == 1


def test_run_from_environment_falls_back_to_trackfile_path_env_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    flac = tmp_path / "song.flac"
    make_flac(flac)
    monkeypatch.setenv("lidarr_eventtype", "Download")
    monkeypatch.delenv("lidarr_addedtrackpaths", raising=False)
    monkeypatch.setenv("lidarr_trackfile_path", str(flac))

    with patch("lidarr.import_folder") as import_folder:
        result = lidarr_hook.run_from_environment(_settings(tmp_path))

    assert result == 0
    assert (tmp_path / "song.mp3").is_file()
    import_folder.assert_not_called()
