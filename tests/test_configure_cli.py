from pathlib import Path
from unittest.mock import patch

import pytest

import config
import configure_cli
import lidarr


def test_mask_short_value_fully_hidden() -> None:
    assert configure_cli._mask("ab") == "**"


def test_mask_long_value_keeps_prefix() -> None:
    assert configure_cli._mask("abcd1234") == "abcd****"


def test_fresh_config_prompts_and_saves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.ini"
    monkeypatch.setattr(config, "CONFIG_PATH", path)

    answers = iter(["newkey", "http://host:8686", "lidarrkey", "y"])
    with patch("builtins.input", lambda *_: next(answers)), \
            patch("lidarr.check_connection", return_value="1.0"):
        assert configure_cli.run() == 0

    saved = config.read_file(path)
    assert saved == {
        "acoustid_api_key": "newkey",
        "lidarr_url": "http://host:8686",
        "lidarr_api_key": "lidarrkey",
    }


def test_blank_answers_keep_existing_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.ini"
    config.save_file({"acoustid_api_key": "oldkey"}, path)
    monkeypatch.setattr(config, "CONFIG_PATH", path)

    answers = iter(["", "http://newhost:8686", "", "y"])
    with patch("builtins.input", lambda *_: next(answers)):
        assert configure_cli.run() == 0

    saved = config.read_file(path)
    assert saved["acoustid_api_key"] == "oldkey"
    assert saved["lidarr_url"] == "http://newhost:8686"


def test_declining_save_leaves_file_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.ini"
    monkeypatch.setattr(config, "CONFIG_PATH", path)

    answers = iter(["newkey", "", "", "n"])
    with patch("builtins.input", lambda *_: next(answers)):
        assert configure_cli.run() == 1

    assert not path.exists()


def test_nothing_changed_skips_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.ini"
    monkeypatch.setattr(config, "CONFIG_PATH", path)

    answers = iter(["", "", ""])
    with patch("builtins.input", lambda *_: next(answers)):
        assert configure_cli.run() == 0

    assert not path.exists()


def test_env_override_skips_prompt_and_is_not_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.ini"
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setenv("LIDARR_URL", "http://env:8686")

    answers = iter(["", "ignored-because-env-wins", "", "y"])
    with patch("builtins.input", lambda *_: next(answers)):
        assert configure_cli.run() == 0

    saved = config.read_file(path)
    assert "lidarr_url" not in saved


def test_verify_skipped_when_lidarr_incomplete() -> None:
    assert configure_cli._verify({"lidarr_url": "http://host:8686"}) is True
    assert configure_cli._verify({}) is True


def test_verify_passes_on_successful_connection() -> None:
    merged = {"lidarr_url": "http://host:8686", "lidarr_api_key": "key123"}
    with patch("lidarr.check_connection", return_value="1.0") as check:
        assert configure_cli._verify(merged) is True
    check.assert_called_once_with("http://host:8686", "key123")


def test_verify_fails_on_rejected_connection() -> None:
    merged = {"lidarr_url": "http://host:8686", "lidarr_api_key": "wrongkey"}
    with patch("lidarr.check_connection", side_effect=lidarr.LidarrError("Lidarr rejected the API key")):
        assert configure_cli._verify(merged) is False


def test_verify_uses_env_override_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIDARR_URL", "http://env:8686")
    merged = {"lidarr_url": "http://file:8686", "lidarr_api_key": "key123"}
    with patch("lidarr.check_connection", return_value="1.0") as check:
        assert configure_cli._verify(merged) is True
    check.assert_called_once_with("http://env:8686", "key123")


def test_failed_verification_defaults_to_not_saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.ini"
    monkeypatch.setattr(config, "CONFIG_PATH", path)

    answers = iter(["", "http://host:8686", "badkey", ""])
    with patch("builtins.input", lambda *_: next(answers)), \
            patch("lidarr.check_connection", side_effect=lidarr.LidarrError("Lidarr rejected the API key")):
        assert configure_cli.run() == 1

    assert not path.exists()


def test_failed_verification_can_be_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.ini"
    monkeypatch.setattr(config, "CONFIG_PATH", path)

    answers = iter(["", "http://host:8686", "badkey", "y"])
    with patch("builtins.input", lambda *_: next(answers)), \
            patch("lidarr.check_connection", side_effect=lidarr.LidarrError("Lidarr rejected the API key")):
        assert configure_cli.run() == 0

    saved = config.read_file(path)
    assert saved["lidarr_api_key"] == "badkey"
