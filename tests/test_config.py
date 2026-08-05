from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

import config


def _qsettings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    assert config.load_settings(config_path=tmp_path / "nope.ini") == {}


def test_malformed_ini_returns_empty(tmp_path: Path) -> None:
    bad = tmp_path / "config.ini"
    bad.write_text("not [ valid ini", encoding="utf-8")
    assert config.load_settings(config_path=bad) == {}


def test_reads_fields_from_file(tmp_path: Path) -> None:
    path = tmp_path / "config.ini"
    path.write_text(
        "[flac2mp3]\n"
        "acoustid_api_key = filekey\n"
        "lidarr_url = http://file:8686\n"
        "lidarr_api_key = filelidarrkey\n",
        encoding="utf-8",
    )
    assert config.load_settings(config_path=path) == {
        "acoustid_api_key": "filekey",
        "lidarr_url": "http://file:8686",
        "lidarr_api_key": "filelidarrkey",
    }


def test_qsettings_overrides_file(tmp_path: Path) -> None:
    path = tmp_path / "config.ini"
    path.write_text("[flac2mp3]\nacoustid_api_key = filekey\n", encoding="utf-8")
    settings = _qsettings(tmp_path)
    settings.setValue("acoustid_api_key", "guikey")
    assert config.load_settings(settings, config_path=path)["acoustid_api_key"] == "guikey"


def test_env_var_overrides_qsettings_and_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.ini"
    path.write_text("[flac2mp3]\nacoustid_api_key = filekey\n", encoding="utf-8")
    settings = _qsettings(tmp_path)
    settings.setValue("acoustid_api_key", "guikey")
    monkeypatch.setenv("ACOUSTID_API_KEY", "envkey")
    assert config.load_settings(settings, config_path=path)["acoustid_api_key"] == "envkey"


def test_precedence_order_full_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.ini"
    path.write_text(
        "[flac2mp3]\n"
        "acoustid_api_key = filekey\n"
        "lidarr_url = http://file:8686\n"
        "lidarr_api_key = filelidarrkey\n",
        encoding="utf-8",
    )
    settings = _qsettings(tmp_path)
    settings.setValue("lidarr_url", "http://gui:8686")
    monkeypatch.setenv("LIDARR_API_KEY", "envlidarrkey")

    result = config.load_settings(settings, config_path=path)

    assert result["acoustid_api_key"] == "filekey"
    assert result["lidarr_url"] == "http://gui:8686"
    assert result["lidarr_api_key"] == "envlidarrkey"


def test_no_qsettings_uses_file_only(tmp_path: Path) -> None:
    path = tmp_path / "config.ini"
    path.write_text("[flac2mp3]\nlidarr_url = http://file:8686\n", encoding="utf-8")
    assert config.load_settings(config_path=path) == {"lidarr_url": "http://file:8686"}


def test_script_dir_uses_argv0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", [str(tmp_path / "sidecut.py")])
    assert config.script_dir() == tmp_path


def test_resolve_config_path_prefers_script_dir_when_file_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.argv", [str(tmp_path / "sidecut.py")])
    local = tmp_path / "config.ini"
    local.write_text("[flac2mp3]\n", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "elsewhere" / "config.ini")

    assert config.resolve_config_path() == local


def test_resolve_config_path_prefers_existing_config_path_over_fresh_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither file is new here: CONFIG_PATH already has real values, no
    local config.ini yet - must not silently switch to (empty) local and
    hide the existing config."""
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    monkeypatch.setattr("sys.argv", [str(script_dir / "sidecut.py")])
    existing = tmp_path / "elsewhere" / "config.ini"
    existing.parent.mkdir()
    existing.write_text("[flac2mp3]\n", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", existing)

    assert config.resolve_config_path() == existing


def test_resolve_config_path_defaults_to_local_on_true_first_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither file exists anywhere yet: default the save target to next
    to the script rather than ~/.config."""
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    monkeypatch.setattr("sys.argv", [str(script_dir / "sidecut.py")])
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "elsewhere" / "config.ini")

    assert config.resolve_config_path() == script_dir / "config.ini"


def test_resolve_config_path_falls_back_when_script_dir_unwritable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    monkeypatch.setattr("sys.argv", [str(script_dir / "sidecut.py")])
    fallback = tmp_path / "elsewhere" / "config.ini"
    monkeypatch.setattr(config, "CONFIG_PATH", fallback)
    monkeypatch.setattr("os.access", lambda *_a, **_kw: False)

    assert config.resolve_config_path() == fallback


def test_load_settings_defaults_to_resolved_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", [str(tmp_path / "sidecut.py")])
    local = tmp_path / "config.ini"
    local.write_text("[flac2mp3]\nlidarr_url = http://local:8686\n", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "elsewhere" / "config.ini")

    assert config.load_settings()["lidarr_url"] == "http://local:8686"
