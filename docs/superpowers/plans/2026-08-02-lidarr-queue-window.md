# Lidarr Queue Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a non-modal Qt window that shows Lidarr's live download queue (`GET /api/v1/queue`), auto-opening on `acoustid.py` startup when Lidarr is configured, refreshing every 5 seconds.

**Architecture:** One new backend function `lidarr.get_queue()` following the existing `get_album_trackfiles`/`get_artist_trackfiles` GET pattern. One new `QThread` worker (`LidarrQueueWorker`) and one new `QDialog` (`LidarrQueueWindow`) in `acoustid.py`, following the existing `LidarrConnectionTestWorker`/`LibraryStatsWindow` patterns. `MainWindow.__init__` opens the window automatically if `lidarr_url`/`lidarr_api_key` are already set in `QSettings`.

**Tech Stack:** Python, `requests`, PySide6 (`QDialog`, `QThread`, `QTimer`, `QTableWidget`), `pytest` + `unittest.mock`.

## Global Constraints

- Follow existing `lidarr.py` conventions exactly: `_with_retry`, `_headers`, `_url`, raise `LidarrError` with a descriptive message on `requests.RequestException`.
- Follow existing `acoustid.py` conventions exactly: `QDialog` for standalone windows, `QThread` subclass per async operation with `Signal`s for success/error, `QSettings("AcoustID", "AcoustID")` for config.
- No manual reopen control for the queue window (closing it stops polling for the rest of the app session — by design, see spec).
- No queue item actions (remove/retry/prioritize) — read-only.
- Poll interval: 5000ms, fixed (no user setting).
- Full spec: `docs/superpowers/specs/2026-08-02-lidarr-queue-window-design.md`.

---

### Task 1: `lidarr.get_queue()`

**Files:**
- Modify: `lidarr.py` (add function after `get_artist_trackfiles`, i.e. after line 449, before `delete_trackfile`)
- Test: `tests/test_lidarr.py` (add tests after `test_get_album_trackfiles_passes_album_id`, i.e. after line 568)

**Interfaces:**
- Consumes: `lidarr._with_retry`, `lidarr._headers`, `lidarr._url`, `lidarr.LidarrError`, `lidarr.REQUEST_TIMEOUT` (all already defined in `lidarr.py`).
- Produces: `get_queue(base_url: str, api_key: str) -> list[dict[str, Any]]`, used by Task 2's `LidarrQueueWorker`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_lidarr.py` right after `test_get_album_trackfiles_passes_album_id` (after line 568):

```python
def test_get_queue_returns_records() -> None:
    response = Mock()
    response.json.return_value = {"records": [{"id": 1, "title": "Some Album"}], "totalRecords": 1}

    with patch("requests.get", return_value=response) as get:
        queue = lidarr.get_queue("http://localhost:8686", "key")

    assert queue == [{"id": 1, "title": "Some Album"}]
    assert get.call_args.kwargs["params"] == {"pageSize": 200, "includeAlbum": True, "includeArtist": True}
    assert get.call_args.kwargs["headers"] == {"X-Api-Key": "key"}


def test_get_queue_raises_on_request_failure() -> None:
    with patch("requests.get", side_effect=requests.ConnectionError("no route")), patch("time.sleep"):
        with pytest.raises(lidarr.LidarrError, match="Failed to fetch the Lidarr queue"):
            lidarr.get_queue("http://localhost:8686", "key")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/sigma/git/flac2mp3 && python3 -m pytest tests/test_lidarr.py -k test_get_queue -v`
Expected: FAIL with `AttributeError: module 'lidarr' has no attribute 'get_queue'`

- [ ] **Step 3: Implement `get_queue`**

Add to `lidarr.py` after `get_artist_trackfiles` (after line 449, before `def delete_trackfile`):

```python
def get_queue(base_url: str, api_key: str) -> list[dict[str, Any]]:
    """List Lidarr's current download queue (GET /api/v1/queue) - what
    Lidarr is currently downloading/importing, from any source. Independent
    of commands this tool queues itself (see submit_manual_import etc)."""
    try:
        response = _with_retry(
            requests.get,
            _url(base_url, "/api/v1/queue"),
            headers=_headers(api_key),
            params={"pageSize": 200, "includeAlbum": True, "includeArtist": True},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise LidarrError(f"Failed to fetch the Lidarr queue: {exc}") from exc
    return response.json()["records"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/sigma/git/flac2mp3 && python3 -m pytest tests/test_lidarr.py -k test_get_queue -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run full lidarr test suite to check no regressions**

Run: `cd /home/sigma/git/flac2mp3 && python3 -m pytest tests/test_lidarr.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
cd /home/sigma/git/flac2mp3
git add lidarr.py tests/test_lidarr.py
git commit -m "feat: add lidarr.get_queue for fetching the live download queue"
```

---

### Task 2: `LidarrQueueWorker` and `LidarrQueueWindow`

**Files:**
- Modify: `acoustid.py` (add worker class after `LidarrConnectionTestWorker`, i.e. after line 272/before line 275's `class LidarrImportLogWindow`; add window class after `LibraryStatsWindow`, i.e. after line 553/before line 556's `class DeclutterScanWorker`)

**Interfaces:**
- Consumes: `lidarr.get_queue(base_url, api_key) -> list[dict]` (Task 1), `lidarr.LidarrError`.
- Produces: `LidarrQueueWorker` (QThread, signals `queue_finished(list)` / `queue_error(str)`, constructor `(base_url: str, api_key: str)`), `LidarrQueueWindow` (QDialog, constructor `(base_url: str, api_key: str, parent: QWidget | None = None)`) — both consumed by Task 3's `MainWindow.__init__`.

No automated tests for this task — matches existing convention that Qt dialog/worker classes in `acoustid.py` (`LidarrImportLogWindow`, `LibraryStatsWindow`, `LidarrConnectionTestWorker`, etc.) have no test coverage; verified manually in Task 4.

- [ ] **Step 1: Add `LidarrQueueWorker`**

Insert into `acoustid.py` right after `LidarrConnectionTestWorker` ends (after line 272, before line 275 `class LidarrImportLogWindow(QDialog):`):

```python
class LidarrQueueWorker(QThread):
    """Fetches Lidarr's current download queue off the UI thread."""

    queue_finished = Signal(list)
    queue_error = Signal(str)

    def __init__(self, base_url: str, api_key: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.api_key = api_key

    def run(self) -> None:
        try:
            records = lidarr.get_queue(self.base_url, self.api_key)
        except lidarr.LidarrError as exc:
            self.queue_error.emit(str(exc))
            return
        self.queue_finished.emit(records)
```

- [ ] **Step 2: Add `LidarrQueueWindow`**

Insert into `acoustid.py` right after `LibraryStatsWindow` ends (after line 553, before line 556 `class DeclutterScanWorker(QThread):`):

```python
QUEUE_POLL_INTERVAL_MS = 5000


def _queue_record_title(record: dict[str, Any]) -> str:
    album = record.get("album") or {}
    artist = record.get("artist") or {}
    album_title = album.get("title")
    artist_name = artist.get("artistName")
    if album_title and artist_name:
        return f"{artist_name} - {album_title}"
    if album_title:
        return album_title
    return record.get("title", "")


def _queue_record_status(record: dict[str, Any]) -> str:
    status = str(record.get("status", "")).title()
    error_message = record.get("errorMessage")
    tracked_status = record.get("trackedDownloadStatus")
    if error_message:
        return f"{status}: {error_message}"
    if tracked_status in ("warning", "error"):
        return f"{status} ({tracked_status})"
    return status


def _queue_record_progress(record: dict[str, Any]) -> str:
    size = record.get("size") or 0
    sizeleft = record.get("sizeleft")
    if not size or sizeleft is None:
        return ""
    percent = 100 * (1 - sizeleft / size)
    return f"{percent:.0f}%"


class LidarrQueueWindow(QDialog):
    """Standalone, non-modal window showing Lidarr's live download queue
    (GET /api/v1/queue), polled every QUEUE_POLL_INTERVAL_MS. Read-only -
    no queue item actions. Closing the window stops polling; there is no
    reopen control, by design (see docs/superpowers/specs/2026-08-02-
    lidarr-queue-window-design.md)."""

    COLUMNS = ["Title", "Status", "Quality", "Progress", "Time left"]

    def __init__(self, base_url: str, api_key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.base_url = base_url
        self.api_key = api_key
        self.worker: LidarrQueueWorker | None = None

        self.setWindowTitle("Lidarr Queue")
        self.resize(720, 400)

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        layout.addWidget(self.table)

        self.status_label = QLabel("Loading...")
        layout.addWidget(self.status_label)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(QUEUE_POLL_INTERVAL_MS)
        self._poll()

    def _poll(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self.worker = LidarrQueueWorker(self.base_url, self.api_key)
        self.worker.queue_finished.connect(self._on_queue_finished)
        self.worker.queue_error.connect(self._on_queue_error)
        self.worker.start()

    def _on_queue_finished(self, records: list[Any]) -> None:
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            self.table.setItem(row, 0, QTableWidgetItem(_queue_record_title(record)))
            self.table.setItem(row, 1, QTableWidgetItem(_queue_record_status(record)))
            self.table.setItem(row, 2, QTableWidgetItem((record.get("quality") or {}).get("quality", {}).get("name", "")))
            self.table.setItem(row, 3, QTableWidgetItem(_queue_record_progress(record)))
            self.table.setItem(row, 4, QTableWidgetItem(record.get("timeleft") or ""))
        self.status_label.setText("Queue is empty." if not records else "")

    def _on_queue_error(self, message: str) -> None:
        self.status_label.setText(message)

    def closeEvent(self, event: Any) -> None:
        self.timer.stop()
        super().closeEvent(event)
```

- [ ] **Step 3: Add `Any` to the `typing` import if not already present**

Check line 14 of `acoustid.py` (`from typing import TextIO`) — add `Any`:

```python
from typing import Any, TextIO
```

- [ ] **Step 4: Add `QTimer` to the `PySide6.QtCore` import**

Line 17 currently reads:

```python
from PySide6.QtCore import QEvent, QSettings, Qt, QThread, Signal
```

Change to:

```python
from PySide6.QtCore import QEvent, QSettings, Qt, QThread, QTimer, Signal
```

- [ ] **Step 5: Verify the file parses and imports cleanly**

Run: `cd /home/sigma/git/flac2mp3 && python3 -c "import acoustid"`
Expected: no output, exit code 0 (requires PySide6 installed in the venv — use `.venv/bin/python3` if the system interpreter lacks it: `.venv/bin/python3 -c "import acoustid"`)

- [ ] **Step 6: Commit**

```bash
cd /home/sigma/git/flac2mp3
git add acoustid.py
git commit -m "feat: add LidarrQueueWorker and LidarrQueueWindow"
```

---

### Task 3: Auto-open on startup

**Files:**
- Modify: `acoustid.py:694-716` (`MainWindow.__init__`)

**Interfaces:**
- Consumes: `LidarrQueueWindow` (Task 2), `self.settings` (existing `QSettings` instance already constructed earlier in `__init__`).

- [ ] **Step 1: Add `self.queue_window` init and auto-open logic**

In `MainWindow.__init__`, current code (lines 694-716):

```python
class MainWindow(QMainWindow):
    def __init__(self, initial_folder: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"AcoustID v{__version__}")
        self.resize(760, 480)

        self.files: list[Path] = []
        self.converter: BatchConverter | None = None
        self.lidarr_worker: LidarrImportWorker | None = None
        self.lidarr_force_reimport_plan_worker: LidarrForceReimportPlanWorker | None = None
        self.lidarr_log_window: LidarrImportLogWindow | None = None
        self.library_stats_worker: LibraryStatsWorker | None = None
        self.library_stats_window: LibraryStatsWindow | None = None
        self.declutter_scan_worker: DeclutterScanWorker | None = None
        self.declutter_sort_dialog: DeclutterSortDialog | None = None
        self.settings = QSettings("AcoustID", "AcoustID")
        self._acoustid_only_run = False

        self._build_ui()
        folder = initial_folder or self._last_folder()
        if folder is not None:
            self._set_folder(folder)
```

Replace with:

```python
class MainWindow(QMainWindow):
    def __init__(self, initial_folder: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"AcoustID v{__version__}")
        self.resize(760, 480)

        self.files: list[Path] = []
        self.converter: BatchConverter | None = None
        self.lidarr_worker: LidarrImportWorker | None = None
        self.lidarr_force_reimport_plan_worker: LidarrForceReimportPlanWorker | None = None
        self.lidarr_log_window: LidarrImportLogWindow | None = None
        self.library_stats_worker: LibraryStatsWorker | None = None
        self.library_stats_window: LibraryStatsWindow | None = None
        self.declutter_scan_worker: DeclutterScanWorker | None = None
        self.declutter_sort_dialog: DeclutterSortDialog | None = None
        self.queue_window: LidarrQueueWindow | None = None
        self.settings = QSettings("AcoustID", "AcoustID")
        self._acoustid_only_run = False

        self._build_ui()
        folder = initial_folder or self._last_folder()
        if folder is not None:
            self._set_folder(folder)

        self._open_queue_window_if_configured()

    def _open_queue_window_if_configured(self) -> None:
        lidarr_url = self.settings.value("lidarr_url", "")
        lidarr_api_key = self.settings.value("lidarr_api_key", "")
        if lidarr_url and lidarr_api_key:
            self.queue_window = LidarrQueueWindow(lidarr_url, lidarr_api_key, self)
            self.queue_window.show()
```

- [ ] **Step 2: Verify the file parses and imports cleanly**

Run: `cd /home/sigma/git/flac2mp3 && .venv/bin/python3 -c "import acoustid"`
Expected: no output, exit code 0

- [ ] **Step 3: Run full test suite to check no regressions**

Run: `cd /home/sigma/git/flac2mp3 && python3 -m pytest -v`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
cd /home/sigma/git/flac2mp3
git add acoustid.py
git commit -m "feat: auto-open Lidarr queue window on startup when configured"
```

---

### Task 4: Manual verification

**Files:** none (verification only)

- [ ] **Step 1: Launch the app with Lidarr configured**

Run: `cd /home/sigma/git/flac2mp3 && .venv/bin/python3 acoustid.py`

Ensure `lidarr_url`/`lidarr_api_key` are set first (via the app's existing Lidarr settings dialog if not already configured from a prior session).

Expected: main window opens, and a second "Lidarr Queue" window also opens automatically, showing current queue contents (or "Queue is empty." if nothing is downloading).

- [ ] **Step 2: Verify live refresh**

Trigger a download/import in Lidarr (or wait for an existing one) and confirm the queue window's table updates within ~5 seconds without manual interaction.

- [ ] **Step 3: Verify error handling**

Stop the Lidarr instance (or point `lidarr_url` at an unreachable host via settings, then restart the app) and confirm the queue window shows an inline error message in the status label rather than crashing the app or popping an error dialog.

- [ ] **Step 4: Verify closing stops polling**

Close the queue window, confirm no further worker threads spawn (no exceptions, no CPU churn) and the main window remains fully functional.

- [ ] **Step 5: Verify silent skip when unconfigured**

Clear `lidarr_url`/`lidarr_api_key` via the settings dialog, restart the app, confirm no queue window opens and no error is shown.
