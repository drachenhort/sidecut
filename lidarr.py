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

CAUTION: DELETE /api/v1/trackfile deletes the actual file on disk, not
just the database row. clear_stale_trackfiles() only ever deletes a
record after confirming (via a real filesystem check, honoring
local_root/lidarr_root) that its file is actually gone - never a blanket
sweep of "every record for this album". Do not bypass that check by
calling delete_trackfile() directly on a record without first confirming
the same thing yourself.
"""

from __future__ import annotations

import time
from pathlib import Path, PurePosixPath
from typing import Any

import requests

REQUEST_TIMEOUT = 30.0
# The manual-import scan does real work per file server-side (reading
# embedded tags, matching against releases), so a large folder or a
# loaded Lidarr instance can easily take longer than a normal API call.
MANUAL_IMPORT_SCAN_TIMEOUT = 180.0
COMMAND_POLL_INTERVAL = 1.0
COMMAND_POLL_TIMEOUT = 300.0
# A busy Lidarr instance can easily have other large, unrelated jobs
# ahead of ours in the queue (e.g. a library rescan working through
# hundreds of albums) - that's normal scheduling, not our command being
# stuck, so it gets its own much more generous allowance. Once Lidarr
# actually starts running our command, COMMAND_POLL_TIMEOUT applies as
# normal from that point.
COMMAND_QUEUE_TIMEOUT = 1800.0
# A single ManualImport command covering a huge batch (e.g. importing a
# ~100-track discography in one go) makes Lidarr do a lot of matching/
# moving work synchronously in one request, which can time out or bog
# down an already-loaded instance. Submitting in smaller batches, with a
# short pause between them, keeps each individual command manageable and
# spreads the load out instead of hitting Lidarr with everything at once.
IMPORT_BATCH_SIZE = 20
IMPORT_BATCH_PAUSE = 2.0
# Likewise for clearing stale TrackFile records: a batch spanning many
# albums/tracks means many individual DELETE calls - pace them out rather
# than firing them back-to-back.
TRACKFILE_DELETE_PAUSE = 0.5


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


def remap_path_to_lidarr(folder: Path, local_root: str, lidarr_root: str) -> str:
    """Rewrite `folder` from this machine's view of a shared library to
    Lidarr's own view, for setups where the two see the same content under
    different mount points - e.g. a share mounted at /home/user/Music here
    but /music inside Lidarr's own container. If `folder` isn't under
    `local_root`, or either root is blank, returns `folder` unchanged (as
    a string) since there's nothing sensible to remap."""
    if not local_root or not lidarr_root:
        return str(folder)
    try:
        relative = folder.relative_to(local_root)
    except ValueError:
        return str(folder)
    return str(PurePosixPath(lidarr_root, *relative.parts))


def lidarr_path_to_local(path: str, local_root: str, lidarr_root: str) -> Path:
    """Inverse of remap_path_to_lidarr: rewrite a path Lidarr reports back
    into this machine's view, so callers can check whether the underlying
    file genuinely still exists before deleting anything. No-op (returned
    as-is) if either root is blank, or `path` isn't under `lidarr_root`."""
    if not local_root or not lidarr_root:
        return Path(path)
    try:
        relative = PurePosixPath(path).relative_to(lidarr_root)
    except ValueError:
        return Path(path)
    return Path(local_root, *relative.parts)


def get_artist_id_for_path(base_url: str, api_key: str, folder: str) -> int | None:
    """Look up which artist (if any) Lidarr's library has recorded at
    `folder` or a parent of it. Used to disambiguate a manual-import scan
    when Lidarr can't infer the artist from the folder name alone - e.g.
    two different artists sharing the same name cause Lidarr to give up
    on parsing entirely ("Expected one artist, but found 2") rather than
    guess, leaving every file unmatched with no tags even read. Returns
    None (not an error) if no artist's path matches."""
    try:
        response = requests.get(_url(base_url, "/api/v1/artist"), headers=_headers(api_key), timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LidarrError(f"Failed to look up Lidarr's artist list: {exc}") from exc
    folder = folder.rstrip("/")
    for artist in response.json():
        artist_path = (artist.get("path") or "").rstrip("/")
        if artist_path and (folder == artist_path or folder.startswith(f"{artist_path}/")):
            return artist["id"]
    return None


def get_manual_import_candidates(
    base_url: str, api_key: str, folder: Path | str, artist_id: int | None = None
) -> list[dict[str, Any]]:
    """Ask Lidarr to scan `folder` and propose matches for each audio file,
    the same way its Manual Import screen does. `folder` must be a path as
    Lidarr itself would see it (see remap_path_to_lidarr if this machine
    mounts the same content under a different path). Pass `artist_id` (see
    get_artist_id_for_path) when known, so Lidarr doesn't have to infer the
    artist from the folder name - needed when that's ambiguous (e.g. two
    library artists sharing a name)."""
    params = {"folder": str(folder), "filterExistingFiles": "true", "replaceExistingFiles": "false"}
    if artist_id is not None:
        params["artistId"] = artist_id
    try:
        response = requests.get(
            _url(base_url, "/api/v1/manualimport"),
            headers=_headers(api_key),
            params=params,
            timeout=MANUAL_IMPORT_SCAN_TIMEOUT,
        )
        response.raise_for_status()
    except requests.Timeout as exc:
        raise LidarrError(
            f"Manual import scan of '{folder}' timed out after {MANUAL_IMPORT_SCAN_TIMEOUT:.0f}s. "
            "Lidarr may still be working on it (large folder, or an otherwise busy instance) - "
            "try again in a bit, or scan a smaller subfolder."
        ) from exc
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


def _to_import_file(item: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw GET /api/v1/manualimport candidate - which nests
    artist/album/tracks as full objects, for display - into the flat shape
    POST /api/v1/command's ManualImport actually expects (artistId/albumId/
    trackIds as bare ids). Forwarding the raw GET item as-is silently sends
    artistId=0/albumId=0 and the command never progresses past "queued"."""
    return {
        "path": item["path"],
        "artistId": item["artist"]["id"],
        "albumId": item["album"]["id"],
        "albumReleaseId": item["albumReleaseId"],
        "trackIds": [t["id"] for t in item["tracks"]],
        "quality": item["quality"],
        "indexerFlags": item.get("indexerFlags", 0),
        "disableReleaseSwitching": item.get("disableReleaseSwitching", False),
    }


def submit_manual_import(
    base_url: str, api_key: str, items: list[dict[str, Any]], import_mode: str = "auto"
) -> int:
    """Queue a ManualImport command for the given (already-matched)
    manual-import candidates, as returned by get_manual_import_candidates.
    Returns the queued command's id for wait_for_command."""
    files = [_to_import_file(item) for item in items]
    return _queue_command(
        base_url,
        api_key,
        {"name": "ManualImport", "files": files, "importMode": import_mode, "replaceExistingFiles": False},
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


def get_artist_trackfiles(base_url: str, api_key: str, artist_id: int) -> list[dict[str, Any]]:
    """List Lidarr's existing TrackFile records for an artist, across all
    of their albums."""
    try:
        response = requests.get(
            _url(base_url, "/api/v1/trackfile"),
            headers=_headers(api_key),
            params={"artistId": artist_id},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LidarrError(f"Failed to look up existing track files for artist {artist_id}: {exc}") from exc
    return response.json()


def delete_trackfile(base_url: str, api_key: str, trackfile_id: int) -> None:
    """DELETEs the file on disk, not just the database record - Lidarr
    treats removing a TrackFile as "delete this file". Never call this for
    a record whose file might still be the one you want to keep; see
    clear_stale_trackfiles for the safe, existence-checked way to remove
    only genuinely orphaned records."""
    try:
        response = requests.delete(
            _url(base_url, f"/api/v1/trackfile/{trackfile_id}"), headers=_headers(api_key), timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LidarrError(f"Failed to delete stale track file {trackfile_id}: {exc}") from exc


def _delete_genuinely_stale_trackfiles(
    base_url: str, api_key: str, trackfiles: list[dict[str, Any]], local_root: str, lidarr_root: str
) -> int:
    """Shared safety-checked deletion loop: only ever deletes a record
    after confirming, via a real filesystem check (honoring
    local_root/lidarr_root), that its file is genuinely gone - and
    requires the file's *parent directory* to be visible at all, since if
    we can't even see the containing folder, this machine likely isn't
    looking at the same filesystem Lidarr is, and guessing "stale" in
    that case previously deleted files that were still very much in use.
    When in doubt, a record is left alone: a leftover stale record can
    always be cleared on a later, correctly-configured run, but a wrongly
    deleted file cannot be undone. Returns how many were deleted."""
    deleted = 0
    for trackfile in trackfiles:
        local_path = lidarr_path_to_local(trackfile["path"], local_root, lidarr_root)
        if not local_path.parent.is_dir():
            continue
        if local_path.exists():
            continue
        if deleted:
            time.sleep(TRACKFILE_DELETE_PAUSE)
        delete_trackfile(base_url, api_key, trackfile["id"])
        deleted += 1
    return deleted


def clear_stale_trackfiles(
    base_url: str, api_key: str, album_id: int, local_root: str = "", lidarr_root: str = ""
) -> int:
    """Delete only the TrackFile records for `album_id` whose file is
    genuinely gone from disk - never a blanket "clear the album" sweep.
    DELETE /api/v1/trackfile removes the actual file, not just the
    database row, so deleting a record for a file that's still there
    would destroy real data; see _delete_genuinely_stale_trackfiles for
    the safety check. Returns how many genuinely stale records were
    deleted."""
    trackfiles = get_album_trackfiles(base_url, api_key, album_id)
    return _delete_genuinely_stale_trackfiles(base_url, api_key, trackfiles, local_root, lidarr_root)


def clear_stale_trackfiles_for_artist(
    base_url: str, api_key: str, artist_id: int, local_root: str = "", lidarr_root: str = ""
) -> int:
    """Like clear_stale_trackfiles, but across every album of an artist.

    Meant to run proactively before scanning a whole-artist folder: if
    even one track's file is stale (this tool deleted/replaced it, e.g.
    converting a FLAC to MP3), Lidarr's manual-import scan doesn't cleanly
    reject it - it crashes with a 500 (System.IO.FileNotFoundException
    from its AugmentingService trying to read that missing file), which
    aborts the *entire* scan, not just that one file. Clearing stale
    records first avoids that crash happening at all, rather than reacting
    to it after the fact. Returns how many genuinely stale records were
    deleted."""
    trackfiles = get_artist_trackfiles(base_url, api_key, artist_id)
    return _delete_genuinely_stale_trackfiles(base_url, api_key, trackfiles, local_root, lidarr_root)


def wait_for_command(
    base_url: str,
    api_key: str,
    command_id: int,
    timeout: float = COMMAND_POLL_TIMEOUT,
    queue_timeout: float = COMMAND_QUEUE_TIMEOUT,
) -> dict[str, Any]:
    """Poll a queued command until Lidarr reports it finished, returning
    the final command status payload.

    Two separate budgets, since "queued" and "started" mean very
    different things: `queue_timeout` covers time spent merely queued
    (Lidarr hasn't picked it up yet - typically because something else,
    unrelated to us, is ahead of it, like a large library rescan; that's
    normal scheduling, not a stuck command, so it gets a generous
    allowance), while `timeout` covers time spent actually running, which
    starts counting fresh once Lidarr reports "started"."""
    queue_deadline = time.monotonic() + queue_timeout
    started_deadline: float | None = None
    url = _url(base_url, f"/api/v1/command/{command_id}")
    while True:
        try:
            response = requests.get(url, headers=_headers(api_key), timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise LidarrError(f"Failed to poll the Lidarr import's status: {exc}") from exc
        data = response.json()
        status = data.get("status")
        if status in ("completed", "failed"):
            return data
        now = time.monotonic()
        if status == "started":
            if started_deadline is None:
                started_deadline = now + timeout
            if now > started_deadline:
                raise LidarrError("Timed out waiting for Lidarr to finish the import")
        elif now > queue_deadline:
            raise LidarrError(
                f"Command is still queued after {queue_timeout:.0f}s - Lidarr appears to be busy with other "
                "work (e.g. a large library rescan). It will likely still complete on its own; check back later."
            )
        time.sleep(COMMAND_POLL_INTERVAL)


def import_folder(
    base_url: str,
    api_key: str,
    folder: Path,
    import_mode: str = "auto",
    local_root: str = "",
    lidarr_root: str = "",
) -> tuple[int, int, list[str]]:
    """Scan `folder` via Lidarr's manual-import endpoint and submit the
    candidates Lidarr fully auto-matched. For candidates Lidarr rejected
    specifically because it already has a file for that track (the
    standard symptom of this tool having deleted/replaced a file Lidarr's
    database doesn't know is gone), the stale TrackFile record is deleted
    and the scan retried once before giving up on those.

    `folder` is this machine's path. If Lidarr sees the same content under
    a different mount point (e.g. Lidarr runs elsewhere/in a container),
    pass `local_root`/`lidarr_root` (see remap_path_to_lidarr) so the
    folder sent to Lidarr's API matches its own filesystem view - otherwise
    Lidarr will silently find nothing at a path that doesn't exist for it.

    A large batch of matched files is submitted in chunks of
    IMPORT_BATCH_SIZE (with a short pause between chunks) rather than one
    huge command, so importing e.g. a full discography at once doesn't
    hand Lidarr one enormous synchronous operation to process.

    Returns (imported_count, skipped_count, skipped_file_descriptions),
    where each skipped description includes Lidarr's own rejection reason.
    Never raises for individual unmatched files - only for connectivity/API
    failures or a Lidarr-reported import failure (which stops further
    batches - already-imported batches stay imported)."""
    lidarr_folder = remap_path_to_lidarr(folder, local_root, lidarr_root)
    # Resolving the artist by path up front means Lidarr never has to guess
    # it from the folder name - which it can't do at all when two library
    # artists share a name, and then gives up on parsing the whole folder.
    artist_id = get_artist_id_for_path(base_url, api_key, lidarr_folder)
    if artist_id is not None:
        # Proactively clear this artist's stale trackfiles before scanning
        # at all: even one file's stale record can crash the whole scan
        # (see clear_stale_trackfiles_for_artist), not just get politely
        # rejected, so waiting to react to a rejection is too late here.
        clear_stale_trackfiles_for_artist(base_url, api_key, artist_id, local_root, lidarr_root)
    candidates = get_manual_import_candidates(base_url, api_key, lidarr_folder, artist_id)

    stale_album_ids = {c["album"]["id"] for c in candidates if has_existing_file_rejection(c)}
    for i, album_id in enumerate(stale_album_ids):
        if i:
            time.sleep(TRACKFILE_DELETE_PAUSE)
        clear_stale_trackfiles(base_url, api_key, album_id, local_root, lidarr_root)
    if stale_album_ids:
        candidates = get_manual_import_candidates(base_url, api_key, lidarr_folder, artist_id)

    matched = [c for c in candidates if is_fully_matched(c)]
    skipped = [c for c in candidates if not is_fully_matched(c)]

    imported = 0
    for i in range(0, len(matched), IMPORT_BATCH_SIZE):
        if i:
            time.sleep(IMPORT_BATCH_PAUSE)
        batch = matched[i : i + IMPORT_BATCH_SIZE]
        command_id = submit_manual_import(base_url, api_key, batch, import_mode)
        result = wait_for_command(base_url, api_key, command_id)
        if result.get("status") == "failed":
            raise LidarrError(f"Lidarr reported the import failed: {result.get('message', 'unknown error')}")
        imported += len(batch)

    skipped_names = [f"{Path(c.get('path', '?')).name}: {skip_reason(c)}" for c in skipped]
    return imported, len(skipped), skipped_names
