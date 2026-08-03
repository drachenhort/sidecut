"""Headless entry point for running this tool as a Lidarr Custom Script.

Register this program (sidecut.py, which is executable and already has a
`#!/usr/bin/env python3` shebang) as a Custom Script under Lidarr's
Settings > Connect, triggered "On Import"/"On Upgrade". Lidarr calls it
directly right after grabbing/importing a release, passing details as
lidarr_* environment variables - the same mechanism TheCaptain989's
lidarr-flac2mp3 script uses (the one behind linuxserver/docker-mods'
lidarr-flac2mp3 mod). No GUI, no folder browsing: this reads which FLACs
Lidarr just added, converts them, and hands the result to Lidarr's Manual
Import API using the Lidarr URL/API key already configured in this app's
own settings (the Lidarr Settings... dialog) - no separate configuration
needed for this hook.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import TextIO

from PySide6.QtCore import QSettings

import core
import lidarr

# Lidarr calls custom scripts for every connection event, not just imports;
# these are the only two this hook does anything with. Everything else
# (Grab, Rename, TrackRetag, ArtistAdd, ArtistDelete, AlbumDelete,
# ApplicationUpdate, HealthIssue, ManualInteractionRequired) is ignored.
SUPPORTED_EVENTS = {"Download", "Test"}


def is_invocation() -> bool:
    """Whether this process was launched by Lidarr as a Custom Script."""
    return "lidarr_eventtype" in os.environ


def _env(name: str) -> str:
    return os.environ.get(f"lidarr_{name}", "")


def _open_log(log_path: Path) -> TextIO:
    try:
        return log_path.open("a", encoding="utf-8")
    except OSError:
        fallback_dir = Path.home() / ".local" / "share" / "AcoustID" / "logs"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        return (fallback_dir / log_path.name).open("a", encoding="utf-8")


def run_from_environment(settings: QSettings | None = None) -> int:
    """Handle one Lidarr Custom Script invocation using the current
    process's environment variables. Returns a process exit code (0 on
    success, non-zero if fingerprinting/conversion/import hit a problem)."""
    eventtype = _env("eventtype")

    if eventtype == "Test":
        print("Lidarr test event received; nothing to do.")
        return 0
    if eventtype not in SUPPORTED_EVENTS:
        print(f"lidarr_eventtype={eventtype!r} is not handled by this hook; ignoring.")
        return 0

    paths_raw = _env("addedtrackpaths") or _env("trackfile_path")
    if not paths_raw:
        print("No lidarr_addedtrackpaths/lidarr_trackfile_path in the environment; nothing to convert.", file=sys.stderr)
        return 1

    track_paths = [Path(p) for p in paths_raw.split("|") if p]
    flac_paths = [p for p in track_paths if p.suffix.lower() == ".flac"]
    if not flac_paths:
        print("None of the added tracks are FLAC; nothing to convert.")
        return 0

    settings = settings if settings is not None else QSettings("AcoustID", "AcoustID")
    quality = settings.value("quality") or "v0"
    if quality not in core.QUALITY_PRESETS:
        quality = "v0"

    artist_path = _env("artist_path")
    folder = Path(artist_path) if artist_path else flac_paths[0].parent
    log_path = folder / f"acoustid-lidarr-hook-{datetime.now():%Y%m%d-%H%M%S}.log"

    ok_count = 0
    with _open_log(log_path) as log:
        for path in flac_paths:
            result = core.convert_one(path, core.QUALITY_PRESETS[quality], log)
            if result.ok:
                ok_count += 1
            else:
                print(f"Conversion failed for {path}: {result.message}", file=sys.stderr)
    print(f"Converted {ok_count}/{len(flac_paths)} FLAC file(s).")

    lidarr_url = settings.value("lidarr_url", "")
    api_key = settings.value("lidarr_api_key", "")
    if not lidarr_url or not api_key:
        print(
            "No Lidarr URL/API key configured (set them via this app's Lidarr Settings... dialog); "
            "skipping the re-import step. Lidarr's own disk rescan will eventually pick up the new MP3s."
        )
        return 0 if ok_count == len(flac_paths) else 1

    local_root = settings.value("lidarr_local_root", "")
    lidarr_root = settings.value("lidarr_root", "")
    try:
        imported, skipped, skipped_names = lidarr.import_folder(
            lidarr_url, api_key, folder, local_root=local_root, lidarr_root=lidarr_root
        )
    except lidarr.LidarrError as exc:
        print(f"Lidarr import failed: {exc}", file=sys.stderr)
        return 1

    print(f"Lidarr import: {imported} imported, {skipped} skipped.")
    if skipped_names:
        print("Skipped: " + "; ".join(skipped_names))

    return 0 if ok_count == len(flac_paths) else 1
