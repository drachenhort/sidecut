# Lidarr Queue Window

## Purpose

Show the live Lidarr download queue (what Lidarr itself is currently
downloading/importing) in its own window, so the user doesn't need to
switch to the Lidarr web UI to check progress.

## Scope

- Data source: Lidarr's own `GET /api/v1/queue` endpoint. This is
  independent of any commands `lidarr.py` itself queues (manual import,
  force reimport) — it reflects everything Lidarr is currently
  downloading/importing, from any source.
- Window opens automatically on `acoustid.py` startup, if `lidarr_url` and
  `lidarr_api_key` are both already set in `QSettings`. If not configured,
  the window is skipped silently — no error popup on startup.
- Refreshes every 5 seconds while open.
- Closing the window stops polling. No reopen button/menu — if the user
  closes it, it stays closed until the app is restarted. (Deliberately
  minimal; can be revisited if it turns out to be annoying in practice.)

## Backend: `lidarr.py`

New function:

```python
def get_queue(base_url: str, api_key: str) -> list[dict[str, Any]]:
    """List Lidarr's current download queue (GET /api/v1/queue) - what
    Lidarr is currently downloading/importing, from any source. Independent
    of commands this tool queues itself."""
```

- `GET /api/v1/queue` with `params={"pageSize": 200, "includeAlbum": True, "includeArtist": True}`.
- Same `_with_retry` + `LidarrError` pattern as `get_album_trackfiles`/
  `get_artist_trackfiles`.
- Returns the `records` list from the paged response (`response.json()["records"]`).

## UI: `acoustid.py`

### `LidarrQueueWorker(QThread)`

Same shape as `LidarrConnectionTestWorker`: takes `base_url`/`api_key`,
calls `lidarr.get_queue` in `run()`, emits `queue_finished(list)` on
success or `queue_error(str)` on `LidarrError`.

### `LidarrQueueWindow(QDialog)`

- Non-modal, like `LidarrImportLogWindow`/`LibraryStatsWindow`.
- Title: "Lidarr Queue".
- `QTableWidget` with columns: **Title**, **Status**, **Quality**,
  **Progress** (%, computed from `size`/`sizeleft`), **Time left**.
  - Title: prefer `record["album"]["title"]` prefixed with artist name if
    present, else `record["title"]` (release title) as fallback.
  - Status: `record["status"]` (e.g. `downloading`, `queued`,
    `completed`), title-cased for display. If `record.get("errorMessage")`
    or `trackedDownloadStatus == "warning"`/`"error"`, show that message
    instead/appended.
  - Progress: `100 * (1 - sizeleft/size)` when `size` > 0, else blank.
  - Time left: `record.get("timeleft", "")` as returned by Lidarr (already
    a formatted string, e.g. `"00:12:34"`), blank if absent (e.g. stalled).
- Empty queue: table has zero rows, plus a status label reading "Queue is
  empty."
- Owns a `QTimer` (5000ms) started in `__init__`/`showEvent`. Each tick:
  if a `LidarrQueueWorker` isn't already running, start a new one.
- `queue_finished`: repopulate the table from the records list.
- `queue_error`: don't clear the table (keep last-known-good data on
  screen); show the error text in a status label under the table instead.
- `closeEvent`: stop the `QTimer` so no more workers get spawned after the
  window is gone.

### `MainWindow.__init__`

After `self.settings = QSettings(...)` is set up (near the end of
`__init__`, after `_build_ui()`):

```python
lidarr_url = self.settings.value("lidarr_url", "")
lidarr_api_key = self.settings.value("lidarr_api_key", "")
if lidarr_url and lidarr_api_key:
    self.queue_window = LidarrQueueWindow(lidarr_url, lidarr_api_key, self)
    self.queue_window.show()
else:
    self.queue_window = None
```

## Error handling

- Missing Lidarr config at startup: window simply doesn't open (no
  popup — this mirrors how other Lidarr features already behave when
  unconfigured, e.g. `_build_ui`'s existing `if not base_url or not
  api_key` guards).
- Lidarr unreachable/API error during polling: shown inline in the queue
  window's status label; table keeps showing last successful data; polling
  keeps retrying every 5s (no backoff — 5s against a local Lidarr instance
  is cheap, and this mirrors the existing `LidarrConnectionTestWorker`
  which also doesn't back off).

## Testing

- `tests/test_lidarr.py`: unit test `get_queue` against a mocked
  `requests.get` — success path (returns `records` list) and failure path
  (`requests.RequestException` → `LidarrError`), following the existing
  tests for `get_album_trackfiles`/`get_artist_trackfiles`.
- UI (`LidarrQueueWindow`/`LidarrQueueWorker`) is not covered by automated
  tests, consistent with the rest of `acoustid.py`'s Qt classes (no
  existing tests target `LidarrImportLogWindow`, `LibraryStatsWindow`,
  etc. either) — manual verification only, per project convention.

## Out of scope

- No manual reopen control for the queue window.
- No queue item actions (remove, retry, prioritize) — read-only display.
- No persistence of queue window position/size beyond default Qt
  behavior.
