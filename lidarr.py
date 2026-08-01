"""Optional integration with a Lidarr instance's Manual Import API.

Lets converted files be handed straight to Lidarr (which moves/renames them
into its managed library and creates the TrackFile records) without going
through Lidarr's import UI - and without us writing to Lidarr's database
directly, which is unsupported and liable to break across Lidarr versions
or race with Lidarr's own in-memory state.

Follows the same manual-import contract Sonarr/Radarr/Lidarr all share, and
the same approach as the proven TheCaptain989/lidarr-flac2mp3 script (the
one behind linuxserver/docker-mods' lidarr-flac2mp3 mod): GET
/api/v1/manualimport lets Lidarr propose matches (it reads embedded tags
itself, so files carrying MusicBrainz tags - which is everything this tool
converts - are usually matched automatically); when a match is rejected
because Lidarr's database still points at the original file this tool
replaced (e.g. a FLAC it converted to MP3 and deleted), the existing
TrackFile record is deleted via DELETE /api/v1/trackfile/{id} so the
replacement isn't blocked by a stale "already has file" rejection; then
POST /api/v1/command queues the actual import.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests

REQUEST_TIMEOUT = 30.0
COMMAND_POLL_INTERVAL = 1.0
COMMAND_POLL_TIMEOUT = 300.0


class LidarrError(Exception):
    """Raised for any Lidarr connectivity/API/import failure. Callers only
    need to catch this one type - the message is always human-readable."""


def _headers(api_key: str) -> dict[str, str]:
    return {"X-Api-Key": api_key}


def _url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


def check_connection(base_url: str, api_key: str) -> str:
    """Return Lidarr's version string. Raises LidarrError if unreachable or
    the API key is rejected."""
    try:
        response = requests.get(
            _url(base_url, "/api/v1/system/status"), headers=_headers(api_key), timeout=REQUEST_TIMEOUT
        )
    except requests.RequestException as exc:
        raise LidarrError(f"Could not reach Lidarr at {base_url}: {exc}") from exc
    if response.status_code == 401:
        raise LidarrError("Lidarr rejected the API key")
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise LidarrError(f"Lidarr returned an error: {exc}") from exc
    return response.json().get("version", "unknown")


def get_manual_import_candidates(base_url: str, api_key: str, folder: Path) -> list[dict[str, Any]]:
    """Ask Lidarr to scan `folder` and propose matches for each audio file,
    the same way its Manual Import screen does."""
    try:
        response = requests.get(
            _url(base_url, "/api/v1/manualimport"),
            headers=_headers(api_key),
            params={"folder": str(folder), "filterExistingFiles": "true", "replaceExistingFiles": "false"},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LidarrError(f"Manual import scan failed: {exc}") from exc
    return response.json()


def is_fully_matched(item: dict[str, Any]) -> bool:
    """Whether Lidarr was able to auto-match this candidate to an
    artist/album/track with no unresolved rejections - i.e. it's safe to
    submit for import without a human picking the match."""
    return bool(item.get("artist")) and bool(item.get("album")) and bool(item.get("tracks")) and not item.get(
        "rejections"
    )


def _rejection_reasons(item: dict[str, Any]) -> list[str]:
    return [r.get("reason", str(r)) if isinstance(r, dict) else str(r) for r in item.get("rejections") or []]


def has_existing_file_rejection(item: dict[str, Any]) -> bool:
    """Whether this candidate was rejected specifically because Lidarr's
    database already has a TrackFile for that track - the standard
    "Track already has file"/"existing" rejection you get when this tool
    deleted the original (e.g. a FLAC just converted to MP3) but Lidarr's
    database hasn't been told, so it's still pointing at a file that's
    gone. This is the case clear_stale_trackfiles can fix."""
    return bool(item.get("album")) and any(
        "already has" in reason.lower() or "existing" in reason.lower() for reason in _rejection_reasons(item)
    )


def skip_reason(item: dict[str, Any]) -> str:
    """Human-readable reason a candidate wasn't submitted - surfaces
    Lidarr's own rejection text (e.g. "Track already has file") instead of
    just silently listing the filename, since that's usually exactly what
    explains a surprising "0 imported" result."""
    reasons = _rejection_reasons(item)
    if reasons:
        return "; ".join(reasons)
    if not item.get("artist"):
        return "no artist match"
    if not item.get("album"):
        return "no album match"
    if not item.get("tracks"):
        return "no track match"
    return "unmatched"


def _queue_command(base_url: str, api_key: str, payload: dict[str, Any]) -> int:
    try:
        response = requests.post(
            _url(base_url, "/api/v1/command"), headers=_headers(api_key), json=payload, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LidarrError(f"Failed to queue the '{payload.get('name')}' command: {exc}") from exc
    return response.json()["id"]


def submit_manual_import(
    base_url: str, api_key: str, items: list[dict[str, Any]], import_mode: str = "auto"
) -> int:
    """Queue a ManualImport command for the given (already-matched)
    candidates. Returns the queued command's id for wait_for_command."""
    return _queue_command(
        base_url,
        api_key,
        {"name": "ManualImport", "files": items, "importMode": import_mode, "replaceExistingFiles": False},
    )


def get_album_trackfiles(base_url: str, api_key: str, album_id: int) -> list[dict[str, Any]]:
    """List Lidarr's existing TrackFile records for an album."""
    try:
        response = requests.get(
            _url(base_url, "/api/v1/trackfile"),
            headers=_headers(api_key),
            params={"albumId": album_id},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LidarrError(f"Failed to look up existing track files for album {album_id}: {exc}") from exc
    return response.json()


def delete_trackfile(base_url: str, api_key: str, trackfile_id: int) -> None:
    try:
        response = requests.delete(
            _url(base_url, f"/api/v1/trackfile/{trackfile_id}"), headers=_headers(api_key), timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LidarrError(f"Failed to delete stale track file {trackfile_id}: {exc}") from exc


def clear_stale_trackfiles(base_url: str, api_key: str, album_id: int) -> int:
    """Delete every existing TrackFile record for `album_id`, so a
    replacement (e.g. this tool's MP3 after deleting the FLAC it was
    converted from) isn't rejected as "already has file" against a record
    Lidarr hasn't otherwise reconciled. Returns how many were deleted."""
    trackfiles = get_album_trackfiles(base_url, api_key, album_id)
    for trackfile in trackfiles:
        delete_trackfile(base_url, api_key, trackfile["id"])
    return len(trackfiles)


def wait_for_command(
    base_url: str, api_key: str, command_id: int, timeout: float = COMMAND_POLL_TIMEOUT
) -> dict[str, Any]:
    """Poll a queued command until Lidarr reports it finished (or `timeout`
    seconds elapse), returning the final command status payload."""
    deadline = time.monotonic() + timeout
    url = _url(base_url, f"/api/v1/command/{command_id}")
    while True:
        try:
            response = requests.get(url, headers=_headers(api_key), timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise LidarrError(f"Failed to poll the Lidarr import's status: {exc}") from exc
        data = response.json()
        if data.get("status") in ("completed", "failed"):
            return data
        if time.monotonic() > deadline:
            raise LidarrError("Timed out waiting for Lidarr to finish the import")
        time.sleep(COMMAND_POLL_INTERVAL)


def import_folder(base_url: str, api_key: str, folder: Path, import_mode: str = "auto") -> tuple[int, int, list[str]]:
    """Scan `folder` via Lidarr's manual-import endpoint and submit the
    candidates Lidarr fully auto-matched. For candidates Lidarr rejected
    specifically because it already has a file for that track (the
    standard symptom of this tool having deleted/replaced a file Lidarr's
    database doesn't know is gone), the stale TrackFile record is deleted
    and the scan retried once before giving up on those.

    Returns (imported_count, skipped_count, skipped_file_descriptions),
    where each skipped description includes Lidarr's own rejection reason.
    Never raises for individual unmatched files - only for connectivity/API
    failures or a Lidarr-reported import failure."""
    candidates = get_manual_import_candidates(base_url, api_key, folder)

    stale_album_ids = {c["album"]["id"] for c in candidates if has_existing_file_rejection(c)}
    if stale_album_ids:
        for album_id in stale_album_ids:
            clear_stale_trackfiles(base_url, api_key, album_id)
        candidates = get_manual_import_candidates(base_url, api_key, folder)

    matched = [c for c in candidates if is_fully_matched(c)]
    skipped = [c for c in candidates if not is_fully_matched(c)]

    if matched:
        command_id = submit_manual_import(base_url, api_key, matched, import_mode)
        result = wait_for_command(base_url, api_key, command_id)
        if result.get("status") == "failed":
            raise LidarrError(f"Lidarr reported the import failed: {result.get('message', 'unknown error')}")

    skipped_names = [f"{Path(c.get('path', '?')).name}: {skip_reason(c)}" for c in skipped]
    return len(matched), len(skipped), skipped_names
