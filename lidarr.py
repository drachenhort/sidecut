"""Optional integration with a Lidarr instance's Manual Import API.

Lets converted files be handed straight to Lidarr (which moves/renames them
into its managed library and creates the TrackFile records) without going
through Lidarr's import UI - and without us writing to Lidarr's database
directly, which is unsupported and liable to break across Lidarr versions
or race with Lidarr's own in-memory state.

Follows the same manual-import contract Sonarr/Radarr/Lidarr all share:
GET /api/v1/manualimport lets Lidarr propose matches (it reads embedded
tags itself, so files carrying MusicBrainz tags - which is everything this
tool converts - are usually matched automatically); POST /api/v1/command
with those matches queues the actual import.
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import Any

import requests

REQUEST_TIMEOUT = 30.0
COMMAND_POLL_INTERVAL = 1.0
COMMAND_POLL_TIMEOUT = 300.0
RESCAN_POLL_TIMEOUT = 600.0


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
            params={"folder": str(folder), "filterExistingFiles": "true"},
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


def skip_reason(item: dict[str, Any]) -> str:
    """Human-readable reason a candidate wasn't submitted - surfaces
    Lidarr's own rejection text (e.g. "Track already has file") instead of
    just silently listing the filename, since that's usually exactly what
    explains a surprising "0 imported" result: Lidarr's database hasn't
    caught up with a file this tool deleted/replaced outside of Lidarr."""
    reasons = [r.get("reason", str(r)) if isinstance(r, dict) else str(r) for r in item.get("rejections") or []]
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
    base_url: str, api_key: str, items: list[dict[str, Any]], import_mode: str = "move"
) -> int:
    """Queue a ManualImport command for the given (already-matched)
    candidates. Returns the queued command's id for wait_for_command."""
    return _queue_command(
        base_url, api_key, {"name": "ManualImport", "files": items, "importMode": import_mode}
    )


def trigger_rescan(base_url: str, api_key: str) -> int:
    """Queue a library rescan so Lidarr's database catches up with files
    this tool changed on disk outside of Lidarr (e.g. a FLAC it deleted
    after converting it to MP3). Returns the queued command's id."""
    return _queue_command(base_url, api_key, {"name": "RescanFolders"})


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


def import_folder(base_url: str, api_key: str, folder: Path, import_mode: str = "move") -> tuple[int, int, list[str]]:
    """Rescan the library (best-effort, so Lidarr's database reflects files
    this tool just converted/deleted rather than rejecting matches against
    stale trackfile records), then scan `folder` via Lidarr's manual-import
    endpoint and submit only the candidates Lidarr fully auto-matched.
    Returns (imported_count, skipped_count, skipped_file_descriptions),
    where each skipped description includes Lidarr's own rejection reason.
    Never raises for individual unmatched files - only for connectivity/API
    failures or a Lidarr-reported import failure."""
    with contextlib.suppress(LidarrError):
        rescan_id = trigger_rescan(base_url, api_key)
        wait_for_command(base_url, api_key, rescan_id, timeout=RESCAN_POLL_TIMEOUT)

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
