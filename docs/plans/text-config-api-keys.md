# Plan: Text-based config for API keys (SSH/headless)

## Implementation Progress

- [x] `config.py` with `load_settings()`, `CONFIG_PATH`, precedence env > QSettings > file
- [x] `tests/test_config.py` (missing file, malformed ini, env override, precedence)
- [x] `config.ini.example` + README "Headless/SSH configuration" section
- [x] Wire `lidarr_hook.py` to `config.load_settings()`
- [x] Wire `sidecut.py` Settings dialog to seed from file when QSettings empty
- [x] Update CHANGELOG.md `[Unreleased]`
- [x] Manual/automated verification (test suite green)
- [x] `sidecut.py --configure`: interactive text UI (`configure_cli.py`)
      to edit config.ini over SSH without hand-editing the file

## Problem

API keys (`acoustid_api_key`, `lidarr_api_key`, `lidarr_url`) only settable
via Sidecut's Settings dialog (`sidecut.py`), stored through `QSettings`.
No way to configure on a headless/SSH box (e.g. Unraid) without launching
the GUI.

## Goal

Let user drop/edit a plain text config file over SSH and have both
`sidecut.py` (GUI) and `lidarr_hook.py` (headless import hook) pick it up,
with `QSettings` still working for GUI users.

## Design

- New module `config.py`:
  - Read `~/.config/flac2mp3/config.ini` (or `$XDG_CONFIG_HOME`) via
    `configparser`. Fields: `acoustid_api_key`, `lidarr_url`,
    `lidarr_api_key`.
  - `load_settings() -> dict[str, str]` merges: file config as base,
    `QSettings` values override if present (keeps GUI editing working,
    avoids silently ignoring dialog changes).
  - Optional: support env var overrides (`ACOUSTID_API_KEY`,
    `LIDARR_URL`, `LIDARR_API_KEY`) for docker/unraid template use -
    highest priority.
  - Write a commented example file `config.ini.example` at repo root /
    docs, documenting keys.

- `sidecut.py`:
  - On startup, seed `QSettings` defaults from `config.py.load_settings()`
    so Settings dialog pre-fills from file if `QSettings` empty.
  - No behavior change once user has saved via dialog (QSettings wins).

- `lidarr_hook.py`:
  - Already headless-capable; swap direct `QSettings(...)` lookups for
    `config.load_settings()` so it works with zero GUI interaction ever
    having happened.

- Precedence order (highest wins): env vars > QSettings (GUI-saved) >
  config.ini file > built-in empty default.

## Steps

1. Write `config.py` with `load_settings()`, `CONFIG_PATH` constant, tests
   in `tests/test_config.py` (missing file, malformed ini, env override,
   precedence order).
2. Add `config.ini.example` + README section: "Headless/SSH configuration".
3. Wire `lidarr_hook.py` to use `config.load_settings()` instead of raw
   `QSettings`.
4. Wire `sidecut.py` Settings dialog init to seed from file when
   `QSettings` empty.
5. Update CHANGELOG.md `[Unreleased]`.
6. Manual test: SSH-only, no `$DISPLAY`, write config.ini, run
   `lidarr_hook.py` against real/staged Lidarr instance, confirm API key
   picked up.

## Open questions

- Confirm config dir: `~/.config/flac2mp3/` vs reuse `~/.config/AcoustID/`
  (QSettings org/app name is still "AcoustID" - `sidecut.py:1007`).
- Plaintext API key in file is same trust model as current QSettings ini
  backend (also plaintext) - no regression, but worth noting in README.
