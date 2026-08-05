"""Text-based config file for headless/SSH setups (e.g. Unraid).

sidecut.py's Settings dialog stores keys via QSettings, which requires
launching the GUI once. This module lets the same keys be set by dropping
a plain-text ini file, so lidarr_hook.py can run with zero GUI interaction.

Precedence (highest wins): env vars > QSettings (GUI-saved) > config.ini
next to the script (if present) > config.ini in CONFIG_PATH > built-in
empty default.
"""

from __future__ import annotations

import configparser
import os
import sys
from pathlib import Path

FIELDS = ("acoustid_api_key", "lidarr_url", "lidarr_api_key")

FIELD_LABELS = {
    "acoustid_api_key": "AcoustID API key",
    "lidarr_url": "Lidarr URL",
    "lidarr_api_key": "Lidarr API key",
}

ENV_VARS = {
    "acoustid_api_key": "ACOUSTID_API_KEY",
    "lidarr_url": "LIDARR_URL",
    "lidarr_api_key": "LIDARR_API_KEY",
}

CONFIG_PATH = Path(
    os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
) / "flac2mp3" / "config.ini"


def script_dir() -> Path:
    """Directory the running script (sidecut.py) was launched from,
    however it was invoked (`./sidecut.py`, `python3 sidecut.py`, a full
    path from a Lidarr Custom Script, ...)."""
    return Path(sys.argv[0]).resolve().parent


def resolve_config_path() -> Path:
    """config.ini next to the script wins if it exists (handy for a
    self-contained checkout on a headless box with no real $HOME set up).
    If neither that nor CONFIG_PATH exists yet (first run), defaults to
    creating it next to the script rather than under CONFIG_PATH, as long
    as that directory is writable - otherwise CONFIG_PATH."""
    local = script_dir() / "config.ini"
    if local.is_file():
        return local
    if CONFIG_PATH.is_file():
        return CONFIG_PATH
    if os.access(script_dir(), os.W_OK):
        return local
    return CONFIG_PATH


def read_file(path: Path | None = None) -> dict[str, str]:
    """Read just the config file (no QSettings/env merge) - used by
    --configure to show/edit what's actually on disk."""
    if path is None:
        path = resolve_config_path()
    parser = configparser.ConfigParser()
    try:
        with path.open("r", encoding="utf-8") as fh:
            parser.read_file(fh)
    except (OSError, configparser.Error):
        return {}

    section = parser["flac2mp3"] if parser.has_section("flac2mp3") else {}
    return {field: section[field] for field in FIELDS if section.get(field)}


def load_settings(qsettings=None, config_path: Path | None = None) -> dict[str, str]:
    """Merge config file, QSettings, and env vars into one dict of the
    fields in FIELDS, env vars taking highest precedence."""
    result = read_file(config_path)

    if qsettings is not None:
        for field in FIELDS:
            value = qsettings.value(field, "")
            if value:
                result[field] = value

    for field, env_var in ENV_VARS.items():
        value = os.environ.get(env_var, "")
        if value:
            result[field] = value

    return result


def save_file(values: dict[str, str], path: Path | None = None) -> None:
    """Write values over whatever's already in the file (fields not in
    `values` are left untouched), creating the parent dir if needed."""
    if path is None:
        path = resolve_config_path()
    merged = read_file(path)
    merged.update(values)

    path.parent.mkdir(parents=True, exist_ok=True)
    parser = configparser.ConfigParser()
    parser["flac2mp3"] = {field: merged.get(field, "") for field in FIELDS}
    with path.open("w", encoding="utf-8") as fh:
        parser.write(fh)
