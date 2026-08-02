#!/usr/bin/env python3
"""AcoustID: a KDE/Qt window to recursively transcode a folder of FLAC files
to MP3, with an optional AcoustID/MusicBrainz identity check."""

from __future__ import annotations

import os
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from PySide6.QtCharts import QBarCategoryAxis, QBarSet, QChart, QChartView, QHorizontalBarSeries, QValueAxis
from PySide6.QtCore import QEvent, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QEnterEvent, QIcon, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import core
import lidarr
import lidarr_hook
import library_stats

__version__ = "0.7"

STATUS_COLUMN_LABELS = {
    "pending": "Pending",
    "running": "Converting...",
    "ok": "Done",
    "fail": "Failed",
    "checking": "Checking...",
    "checked": "Checked",
}

ACOUSTID_STATUS_LABELS = {
    "match": "Match",
    "mismatch": "Mismatch",
    "identified": "Identified",
    "no_match": "No match",
    "error": "Error",
}


def _format_bytes(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


class BatchConverter(QThread):
    file_started = Signal(int)
    file_progress = Signal(int, float, str)
    file_finished = Signal(int, bool)
    acoustid_checked = Signal(int, str, str, bool)
    batch_finished = Signal(int, int, str, "qint64", "qint64")
    batch_error = Signal(str)

    def __init__(
        self,
        files: list[Path],
        quality: str,
        log_path: Path,
        workers: int,
        acoustid_apikey: str | None = None,
        acoustid_only: bool = False,
        acoustid_autocorrect: bool = False,
    ) -> None:
        super().__init__()
        self.files = files
        self.quality_args = core.QUALITY_PRESETS[quality]
        self.log_path = log_path
        self.workers = workers
        self.acoustid_apikey = acoustid_apikey
        self.acoustid_only = acoustid_only
        # Never rewrite an existing musicbrainz_trackid on a check-only run:
        # "Check AcoustID Only" promises tagged data stays untouched, so
        # this is ignored there. Filling in *missing* release-type/date/
        # originaldate tags is purely additive (never overwrites anything)
        # and safe to allow on both check-only buttons - see
        # acoustid_fill_release_type below.
        self.acoustid_autocorrect = acoustid_autocorrect and not acoustid_only
        self.acoustid_fill_release_type = acoustid_autocorrect
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def _check_acoustid(self, index: int, path: Path, log) -> core.AcoustIDCheck:
        result = core.check_acoustid(path, self.acoustid_apikey)
        if self.acoustid_autocorrect:
            core.correct_acoustid_mismatch(path, result)
        if self.acoustid_fill_release_type and core.apply_release_type(path, result):
            log.write(f"AcoustID: {path}: filled missing release type '{result.release_type}'\n")
        if self.acoustid_fill_release_type and core.apply_release_provenance(path, result):
            log.write(f"AcoustID: {path}: filled missing date/originaldate ({result.date}/{result.originaldate})\n")
        log.write(f"AcoustID [{result.status}]: {path}: {result.detail}\n")
        self.acoustid_checked.emit(index, result.status, result.detail, result.corrected)
        return result

    def _convert(self, index: int, path: Path, log) -> core.ConversionResult:
        if self.acoustid_only:
            self.file_started.emit(index)
            result = self._check_acoustid(index, path, log)
            ok = result.status != "error" and not self._cancel_event.is_set()
            self.file_finished.emit(index, ok)
            return core.ConversionResult(path, ok)

        if self.acoustid_apikey and not self._cancel_event.is_set():
            self._check_acoustid(index, path, log)
        self.file_started.emit(index)
        on_progress = lambda p: self.file_progress.emit(index, p.percent, p.speed)  # noqa: E731
        result = core.convert_one(
            path, self.quality_args, log, on_progress=on_progress, should_cancel=self._cancel_event.is_set
        )
        self.file_finished.emit(index, result.ok)
        return result

    class _LockedLog:
        """Serializes writes from multiple worker threads so log lines from
        different files never interleave mid-write."""

        def __init__(self, log: TextIO) -> None:
            self._log = log
            self._lock = threading.Lock()

        def write(self, text: str) -> None:
            with self._lock:
                self._log.write(text)

    def _open_log(self) -> TextIO:
        try:
            return self.log_path.open("a", encoding="utf-8")
        except OSError:
            fallback_dir = Path.home() / ".local" / "share" / "AcoustID" / "logs"
            fallback_dir.mkdir(parents=True, exist_ok=True)
            self.log_path = fallback_dir / self.log_path.name
            return self.log_path.open("a", encoding="utf-8")

    def run(self) -> None:
        try:
            log = self._open_log()
        except OSError as e:
            self.batch_error.emit(f"Could not write log file: {e}")
            return

        with log, ThreadPoolExecutor(self.workers) as pool:
            locked_log = self._LockedLog(log)
            futures = [pool.submit(self._convert, i, f, locked_log) for i, f in enumerate(self.files)]
            results = [f.result() for f in futures]

        ok_count = sum(r.ok for r in results)
        fail_count = len(results) - ok_count
        src_bytes = sum(r.src_bytes for r in results if r.ok)
        dst_bytes = sum(r.dst_bytes for r in results if r.ok)
        self.batch_finished.emit(ok_count, fail_count, str(self.log_path), src_bytes, dst_bytes)


class LidarrForceReimportPlanWorker(QThread):
    """Runs lidarr.plan_force_reimport() off the UI thread - a read-only
    dry-run preview of what Force Reimport would do, so ForceReimportPreviewDialog
    can show real data instead of a generic warning."""

    plan_finished = Signal(object)  # list[tuple[dict, Path]]
    plan_error = Signal(str)

    def __init__(
        self, base_url: str, api_key: str, folder: Path, local_root: str = "", lidarr_root: str = ""
    ) -> None:
        super().__init__()
        self.base_url = base_url
        self.api_key = api_key
        self.folder = folder
        self.local_root = local_root
        self.lidarr_root = lidarr_root

    def run(self) -> None:
        try:
            in_scope = lidarr.plan_force_reimport(
                self.base_url, self.api_key, self.folder, self.local_root, self.lidarr_root
            )
        except lidarr.LidarrError as exc:
            self.plan_error.emit(str(exc))
            return
        self.plan_finished.emit(in_scope)


class LidarrImportWorker(QThread):
    """Hands a folder to Lidarr's Manual Import API in the background so the
    UI doesn't block on the network round-trip. Entirely independent of
    BatchConverter/self.files - it just tells Lidarr which folder to scan."""

    import_finished = Signal(int, int, str)  # imported_count, skipped_count, "; "-joined skipped names
    import_error = Signal(str)
    import_progress = Signal(str)  # one line per step, e.g. "Submitting batch 2/5..."

    def __init__(
        self,
        base_url: str,
        api_key: str,
        folder: Path,
        local_root: str = "",
        lidarr_root: str = "",
        force: bool = False,
    ) -> None:
        super().__init__()
        self.base_url = base_url
        self.api_key = api_key
        self.folder = folder
        self.local_root = local_root
        self.lidarr_root = lidarr_root
        # force=True runs lidarr.force_reimport_folder instead of the
        # plain import_folder - see that function's docstring for how it
        # clears Lidarr's existing TrackFile records under `folder`
        # without ever deleting a file.
        self.force = force

    def run(self) -> None:
        # Qt signal emission is thread-safe: emitting from this worker
        # thread queues the connected slot to run on the receiver's own
        # thread (the GUI thread), so this callback never touches widgets
        # directly.
        import_func = lidarr.force_reimport_folder if self.force else lidarr.import_folder
        try:
            imported, skipped, skipped_names = import_func(
                self.base_url,
                self.api_key,
                self.folder,
                local_root=self.local_root,
                lidarr_root=self.lidarr_root,
                on_progress=self.import_progress.emit,
            )
        except lidarr.LidarrError as exc:
            self.import_error.emit(str(exc))
            return
        self.import_finished.emit(imported, skipped, "; ".join(skipped_names))


class LidarrConnectionTestWorker(QThread):
    """Runs lidarr.check_connection() off the UI thread, since it's a
    network round-trip that could hang if the URL is unreachable."""

    test_finished = Signal(str)  # Lidarr's version string
    test_error = Signal(str)

    def __init__(self, base_url: str, api_key: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.api_key = api_key

    def run(self) -> None:
        try:
            version = lidarr.check_connection(self.base_url, self.api_key)
        except lidarr.LidarrError as exc:
            self.test_error.emit(str(exc))
            return
        self.test_finished.emit(version)


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
        except Exception as exc:  # noqa: BLE001 - malformed API response, report to the UI
            self.queue_error.emit(f"Failed to read the Lidarr queue: {exc}")
            return
        self.queue_finished.emit(records)


class LidarrImportLogWindow(QDialog):
    """Modeless window that streams LidarrImportWorker's progress lines live,
    so a multi-minute import (many albums, many batches) never looks like
    it's just hanging - every step Lidarr responds to shows up here as it
    happens. Stays open after the import finishes/fails so the log can
    still be reviewed, and is reused (cleared, not recreated) across
    consecutive imports."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Lidarr import")
        self.resize(640, 400)
        # Not modal: this must stay usable/visible while the rest of the
        # main window (folder browsing, other actions) stays interactive.
        self.setModal(False)

        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(5000)
        font = self.text.font()
        font.setFamily("monospace")
        self.text.setFont(font)
        layout.addWidget(self.text)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.close)
        layout.addWidget(buttons)

    def reset(self, title: str) -> None:
        self.setWindowTitle(title)
        self.text.clear()

    def append_line(self, message: str) -> None:
        self.text.appendPlainText(message)


class ForceReimportPreviewDialog(QDialog):
    """Dry-run preview for Force Reimport: shows exactly which files
    lidarr.force_reimport_folder would move aside, clear the Lidarr
    record for, and reimport - built from lidarr.plan_force_reimport, a
    read-only call - before anything actually moves, is deleted, or gets
    reimported. Proceed only becomes available after seeing the real
    list; closing/Cancel does nothing at all."""

    def __init__(self, in_scope: list[tuple[dict, Path]], folder: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Force Reimport - Preview")
        self.resize(640, 480)
        self.setModal(True)

        layout = QVBoxLayout(self)
        warning = QLabel(
            "<b>This path is new and not battle-tested - use with caution, and make sure "
            "this is really what you want.</b><br><br>"
            + (
                f"{len(in_scope)} file(s) already tracked by Lidarr under {folder} would be moved aside, "
                "have their Lidarr track file record cleared, moved straight back, then reimported. "
                "No file should ever be deleted, but this does briefly move your real files."
                if in_scope
                else "No existing Lidarr track file records were found under this folder - this would "
                "just run a plain import, identical to the Import to Lidarr button."
            )
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)

        if in_scope:
            file_list = QPlainTextEdit(self)
            file_list.setReadOnly(True)
            file_list.setPlainText("\n".join(str(path.relative_to(folder)) for _trackfile, path in in_scope))
            layout.addWidget(file_list)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        proceed_button = buttons.addButton("Proceed", QDialogButtonBox.AcceptRole)
        proceed_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class _HoverRevealButton(QPushButton):
    """A small button that reveals a masked QLineEdit's real text only
    while the mouse is held over it - no click-to-toggle state to forget
    to turn back off, the field re-masks itself the moment the mouse
    leaves."""

    def __init__(self, line_edit: QLineEdit, parent: QWidget | None = None) -> None:
        super().__init__("👁", parent)
        self.setFixedWidth(28)
        self.setToolTip("Hold to reveal")
        self._line_edit = line_edit

    def enterEvent(self, event: QEnterEvent) -> None:
        self._line_edit.setEchoMode(QLineEdit.Normal)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._line_edit.setEchoMode(QLineEdit.Password)
        super().leaveEvent(event)


class LidarrSettingsDialog(QDialog):
    """Modal dialog for all of this app's settings - Lidarr's URL/API key
    (with a Test Connection button so mistakes like a wrong host/port or
    bad key show up here instead of only surfacing later as a confusing
    Import to Lidarr failure) plus the AcoustID API key."""

    def __init__(self, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.settings = settings
        self.test_worker: LidarrConnectionTestWorker | None = None

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.acoustid_key_edit = QLineEdit(self.settings.value("acoustid_api_key", ""))
        self.acoustid_key_edit.setPlaceholderText("AcoustID API key (get one at acoustid.org)")
        self.acoustid_key_edit.setEchoMode(QLineEdit.Password)
        acoustid_key_row = QHBoxLayout()
        acoustid_key_row.addWidget(self.acoustid_key_edit)
        acoustid_key_row.addWidget(_HoverRevealButton(self.acoustid_key_edit, self))
        form.addRow("AcoustID API key:", acoustid_key_row)

        self.url_edit = QLineEdit(self.settings.value("lidarr_url", ""))
        self.url_edit.setPlaceholderText("http://localhost:8686")
        self.key_edit = QLineEdit(self.settings.value("lidarr_api_key", ""))
        self.key_edit.setPlaceholderText("API key (Lidarr: Settings > General)")
        form.addRow("Lidarr URL:", self.url_edit)
        form.addRow("Lidarr API key:", self.key_edit)

        self.local_root_edit = QLineEdit(self.settings.value("lidarr_local_root", ""))
        self.local_root_edit.setPlaceholderText("e.g. /home/user/Music (leave blank if not needed)")
        self.lidarr_root_edit = QLineEdit(self.settings.value("lidarr_root", ""))
        self.lidarr_root_edit.setPlaceholderText("e.g. /music (leave blank if not needed)")
        form.addRow("Local path to library:", self.local_root_edit)
        form.addRow("Same path inside Lidarr:", self.lidarr_root_edit)
        path_hint = QLabel(
            "Only needed if this machine mounts the library at a different path than Lidarr sees\n"
            "it (e.g. Lidarr runs in a container/on another host). Leave both blank if this app runs\n"
            "on the same machine/path as Lidarr - otherwise Import to Lidarr will silently find nothing."
        )
        path_hint.setWordWrap(True)
        layout.addLayout(form)
        layout.addWidget(path_hint)

        test_row = QHBoxLayout()
        self.test_button = QPushButton("Test Connection")
        self.test_button.clicked.connect(self._test_connection)
        self.test_status_label = QLabel("")
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_status_label, stretch=1)
        layout.addLayout(test_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _test_connection(self) -> None:
        url = self.url_edit.text().strip()
        api_key = self.key_edit.text().strip()
        if not url or not api_key:
            QMessageBox.critical(self, "Lidarr Settings", "Enter both a URL and an API key first.")
            return
        self.test_button.setEnabled(False)
        self.test_status_label.setText("Testing...")
        self.test_worker = LidarrConnectionTestWorker(url, api_key)
        self.test_worker.test_finished.connect(self._on_test_finished)
        self.test_worker.test_error.connect(self._on_test_error)
        self.test_worker.start()

    def _on_test_finished(self, version: str) -> None:
        self.test_button.setEnabled(True)
        self.test_status_label.setText(f"Connected — Lidarr v{version}")

    def _on_test_error(self, message: str) -> None:
        self.test_button.setEnabled(True)
        self.test_status_label.setText("")
        QMessageBox.critical(self, "Lidarr Settings", message)

    def accept(self) -> None:
        self.settings.setValue("acoustid_api_key", self.acoustid_key_edit.text().strip())
        self.settings.setValue("lidarr_url", self.url_edit.text().strip())
        self.settings.setValue("lidarr_api_key", self.key_edit.text().strip())
        self.settings.setValue("lidarr_local_root", self.local_root_edit.text().strip())
        self.settings.setValue("lidarr_root", self.lidarr_root_edit.text().strip())
        super().accept()


class LibraryStatsWorker(QThread):
    """Runs library_stats.scan_release_types()/scan_release_provenance() off
    the UI thread, since walking and tag-reading a whole library can take a
    while."""

    scan_finished = Signal(object, object)  # Counter[str] (types), Counter[str] (provenance)
    scan_error = Signal(str)

    def __init__(self, folder: Path) -> None:
        super().__init__()
        self.folder = folder

    def run(self) -> None:
        try:
            types = library_stats.scan_release_types(self.folder)
            provenance = library_stats.scan_release_provenance(self.folder)
        except Exception as exc:  # noqa: BLE001 - report to the UI, don't die silently
            self.scan_error.emit(str(exc))
            return
        self.scan_finished.emit(types, provenance)


def _build_bar_chart(counts: Counter[str], title: str) -> QChartView:
    """One horizontal bar chart, most common category first - reads better
    than a pie chart once there are more than a handful of categories,
    since exact magnitudes and a long tail of small categories are both
    easy to read off a shared axis."""
    ordered = counts.most_common()  # descending by count

    bar_set = QBarSet("Releases")
    bar_set.append([count for _, count in ordered])

    series = QHorizontalBarSeries()
    series.append(bar_set)
    series.setLabelsVisible(True)

    chart = QChart()
    chart.addSeries(series)
    chart.setTitle(title)
    chart.legend().setVisible(False)

    axis_y = QBarCategoryAxis()
    axis_y.append([f"{label} ({count})" for label, count in ordered])
    chart.addAxis(axis_y, Qt.AlignLeft)
    series.attachAxis(axis_y)

    axis_x = QValueAxis()
    axis_x.setLabelFormat("%d")
    axis_x.setRange(0, ordered[0][1])
    chart.addAxis(axis_x, Qt.AlignBottom)
    series.attachAxis(axis_x)

    chart_view = QChartView(chart)
    chart_view.setRenderHint(QPainter.Antialiasing)
    return chart_view


class LibraryStatsWindow(QDialog):
    """Standalone, non-modal window showing horizontal bar charts of
    release-type counts (Album, EP, Single, Compilation, ...) and release
    provenance (Original, Reissue, Compilation, Unknown), each most common
    first."""

    def __init__(
        self,
        type_counts: Counter[str],
        provenance_counts: Counter[str],
        folder: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Collection Summary")
        self.resize(720, 800)

        layout = QVBoxLayout(self)
        total = sum(type_counts.values())
        header = QLabel(f"<b>{total}</b> release(s) found under {folder}")
        header.setWordWrap(True)
        layout.addWidget(header)

        if not type_counts:
            layout.addWidget(QLabel("No albums/EPs/singles with readable audio files were found."))
            return

        layout.addWidget(_build_bar_chart(type_counts, "Release types, most common first"))
        layout.addWidget(
            _build_bar_chart(provenance_counts, "Original vs. reissue vs. compilation, most common first")
        )


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
    return record.get("title") or ""


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


def _queue_record_quality(record: dict[str, Any]) -> str:
    quality = (record.get("quality") or {}).get("quality") or {}
    return quality.get("name", "")


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

    def _on_queue_finished(self, records: list[dict[str, Any]]) -> None:
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            self.table.setItem(row, 0, QTableWidgetItem(_queue_record_title(record)))
            self.table.setItem(row, 1, QTableWidgetItem(_queue_record_status(record)))
            self.table.setItem(row, 2, QTableWidgetItem(_queue_record_quality(record)))
            self.table.setItem(row, 3, QTableWidgetItem(_queue_record_progress(record)))
            self.table.setItem(row, 4, QTableWidgetItem(record.get("timeleft") or ""))
        self.status_label.setText("Queue is empty." if not records else "")

    def _on_queue_error(self, message: str) -> None:
        self.status_label.setText(message)

    def closeEvent(self, event: Any) -> None:
        self.timer.stop()
        if self.worker is not None:
            self.worker.wait(5000)
        super().closeEvent(event)


class DeclutterScanWorker(QThread):
    """Runs library_stats.plan_declutter_moves() off the UI thread, since
    walking and tag-reading a whole library can take a while. Read-only -
    nothing moves until DeclutterSortDialog's Move button runs
    DeclutterMoveWorker."""

    scan_finished = Signal(object)  # list[library_stats.DeclutterMove]
    scan_error = Signal(str)

    def __init__(self, folder: Path) -> None:
        super().__init__()
        self.folder = folder

    def run(self) -> None:
        try:
            moves = library_stats.plan_declutter_moves(self.folder)
        except Exception as exc:  # noqa: BLE001 - report to the UI, don't die silently
            self.scan_error.emit(str(exc))
            return
        self.scan_finished.emit(moves)


class DeclutterMoveWorker(QThread):
    """Runs library_stats.execute_declutter_moves() off the UI thread, since
    moving many release folders (especially across network storage, e.g.
    an Unraid share) can take a while."""

    move_finished = Signal(object)  # list[library_stats.DeclutterMove], mutated with .error set

    def __init__(self, moves: list[library_stats.DeclutterMove]) -> None:
        super().__init__()
        self.moves = moves

    def run(self) -> None:
        library_stats.execute_declutter_moves(self.moves)
        self.move_finished.emit(self.moves)


KEEP_LABEL = "Keep as it is"


class DeclutterSortDialog(QDialog):
    """Preview-then-confirm window for moving Reissue and Compilation
    releases into a "Reissues"/"Compilations" subfolder of their own
    artist folder (e.g. "Simple Minds/Album (1998 Remaster)" -> "Simple
    Minds/Reissues/Album (1998 Remaster)", "Simple Minds/Greatest Hits" ->
    "Simple Minds/Compilations/Greatest Hits") - keeps an artist folder
    down to its original studio albums, with every remaster and
    best-of/comp release (which tend to badly overlap in tracklist) tucked
    out of the way instead. Moving files is hard to reverse, so nothing
    happens until the user reviews the list and clicks Move - each row
    also gets a per-release toggle to exclude false positives from
    classify_provenance without leaving the dialog."""

    def __init__(self, moves: list[library_stats.DeclutterMove], folder: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.moves = moves
        self.folder = folder
        self.setWindowTitle("Sort Reissues/Compilations")
        self.resize(760, 480)
        self.setModal(False)

        layout = QVBoxLayout(self)
        header = QLabel(f"<b>{len(moves)}</b> reissue(s)/compilation(s) found under {folder}")
        header.setWordWrap(True)
        layout.addWidget(header)

        if not moves:
            layout.addWidget(QLabel("No releases classified as reissues or compilations were found."))
            buttons = QDialogButtonBox(QDialogButtonBox.Close)
            buttons.rejected.connect(self.close)
            buttons.button(QDialogButtonBox.Close).clicked.connect(self.close)
            layout.addWidget(buttons)
            return

        self.table = QTableWidget(len(moves), 3)
        self.table.setHorizontalHeaderLabels(["Release", "Moves to", "Action"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.combo_boxes: list[QComboBox] = []
        for row, move in enumerate(moves):
            self.table.setItem(row, 0, QTableWidgetItem(str(move.source.relative_to(folder))))
            self.table.setItem(row, 1, QTableWidgetItem(str(move.destination.relative_to(folder))))
            combo = QComboBox()
            # move.destination.parent.name is "Reissues" or "Compilations" -
            # naming the actual destination folder here beats a generic
            # "Sort" label, since both categories share this one dialog.
            combo.addItems([f"Sort into {move.destination.parent.name}", KEEP_LABEL])
            self.table.setCellWidget(row, 2, combo)
            self.combo_boxes.append(combo)
        layout.addWidget(self.table)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        button_row = QHBoxLayout()
        self.move_button = QPushButton(f"Move {len(moves)} Release(s)")
        self.move_button.clicked.connect(self._start_move)
        button_row.addWidget(self.move_button)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        self.move_worker: DeclutterMoveWorker | None = None

    def _start_move(self) -> None:
        for move, combo in zip(self.moves, self.combo_boxes):
            move.selected = combo.currentText() != KEEP_LABEL
        selected_count = sum(1 for move in self.moves if move.selected)
        if not selected_count:
            self.status_label.setText("Nothing selected to move.")
            return
        self.move_button.setEnabled(False)
        self.table.setEnabled(False)
        self.status_label.setText(f"Moving {selected_count} release(s)...")

        self.move_worker = DeclutterMoveWorker(self.moves)
        self.move_worker.move_finished.connect(self._on_move_finished)
        self.move_worker.start()

    def _on_move_finished(self, moves: list[library_stats.DeclutterMove]) -> None:
        ok_count = 0
        fail_count = 0
        for row, move in enumerate(moves):
            self.table.removeCellWidget(row, 2)
            if not move.selected:
                self.table.setItem(row, 2, QTableWidgetItem(KEEP_LABEL))
            elif move.error is None:
                self.table.setItem(row, 2, QTableWidgetItem("Moved"))
                ok_count += 1
            else:
                self.table.setItem(row, 2, QTableWidgetItem(f"Failed: {move.error}"))
                fail_count += 1
        self.status_label.setText(f"Moved: {ok_count}  Failed: {fail_count}  Kept: {len(moves) - ok_count - fail_count}")


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

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        layout.addLayout(self._build_folder_row())
        layout.addLayout(self._build_options_row())
        layout.addLayout(self._build_acoustid_row())
        layout.addLayout(self._build_lidarr_row())

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["File", "Status", "Progress", "AcoustID"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        layout.addWidget(self.table)

        self.overall_bar = QProgressBar()
        layout.addWidget(self.overall_bar)

        self.status_label = QLabel("Choose a folder to scan for FLAC files.")
        layout.addWidget(self.status_label)

    def _build_folder_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setReadOnly(True)
        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._browse_folder)

        self.library_stats_button = QPushButton("Collection Summary")
        self.library_stats_button.setToolTip(
            "Scans this folder recursively (read-only, nothing is modified) and tallies\n"
            "release types (Album, EP, Single, Compilation, Promo, ...) from each release's\n"
            "own tags, showing the result as a bar chart in its own window."
        )
        self.library_stats_button.setEnabled(False)
        self.library_stats_button.clicked.connect(self._start_library_stats_scan)

        self.sort_declutter_button = QPushButton("Sort Reissues/Compilations...")
        self.sort_declutter_button.setToolTip(
            "Scans this folder recursively (read-only) for releases classified as reissues\n"
            "or compilations, then previews moving each into a \"Reissues\"/\"Compilations\"\n"
            "subfolder of its own artist folder - e.g. \"Simple Minds/Album (1998 Remaster)\"\n"
            "-> \"Simple Minds/Reissues/Album (1998 Remaster)\", \"Simple Minds/Greatest Hits\"\n"
            "-> \"Simple Minds/Compilations/Greatest Hits\". Nothing is moved until you review\n"
            "the list and confirm."
        )
        self.sort_declutter_button.setEnabled(False)
        self.sort_declutter_button.clicked.connect(self._start_declutter_scan)

        row.addWidget(QLabel("Folder:"))
        row.addWidget(self.folder_edit, stretch=1)
        row.addWidget(browse_button)
        row.addWidget(self.library_stats_button)
        row.addWidget(self.sort_declutter_button)
        return row

    def _build_options_row(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self.quality_combo = QComboBox()
        for key, label in core.QUALITY_LABELS.items():
            self.quality_combo.addItem(label, userData=key)
        last_quality = self.settings.value("quality")
        if last_quality is not None:
            index = self.quality_combo.findData(last_quality)
            if index != -1:
                self.quality_combo.setCurrentIndex(index)
        self.quality_combo.currentIndexChanged.connect(self._save_quality_setting)

        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, max(1, (os.cpu_count() or 4) * 2))
        self.workers_spin.setValue(min(4, os.cpu_count() or 1))

        self.start_button = QPushButton("Transcode")
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self._start_conversion)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_conversion)

        row.addWidget(QLabel("Quality:"))
        row.addWidget(self.quality_combo)
        row.addWidget(QLabel("Parallel jobs:"))
        row.addWidget(self.workers_spin)
        row.addStretch(1)
        row.addWidget(self.start_button)
        row.addWidget(self.cancel_button)
        return row

    def _build_acoustid_row(self) -> QLayout:
        outer = QVBoxLayout()

        config_row = QHBoxLayout()
        self.acoustid_checkbox = QCheckBox("Check AcoustID")
        self.acoustid_checkbox.setChecked(self.settings.value("acoustid_enabled", False, type=bool))
        self.acoustid_checkbox.toggled.connect(self._save_acoustid_settings)

        self.acoustid_autocorrect_checkbox = QCheckBox("Auto-correct mismatched MBID")
        self.acoustid_autocorrect_checkbox.setToolTip(
            "When Check AcoustID finds a mismatch with a confident enough score "
            f"(>= {core.ACOUSTID_AUTOCORRECT_MIN_SCORE:.1f}), rewrite the FLAC's "
            "musicbrainz_trackid tag to AcoustID's suggested recording before converting.\n"
            "The MBID rewrite never applies during a Check AcoustID Only (or +MP3) run.\n"
            "Also fills in a missing release-type tag (Album/EP/Single/Compilation/...) from "
            "AcoustID's match on FLAC or MP3, whenever the file doesn't already have one - this "
            "is what feeds the Collection Summary's release-type breakdown. Unlike the MBID "
            "rewrite, this only ever adds a missing tag (never overwrites), so it also runs "
            "during both Check AcoustID buttons - handy for backfilling an already-converted "
            "MP3 library."
        )
        self.acoustid_autocorrect_checkbox.setChecked(self.settings.value("acoustid_autocorrect", False, type=bool))
        self.acoustid_autocorrect_checkbox.toggled.connect(self._save_acoustid_settings)

        config_row.addWidget(self.acoustid_checkbox)
        config_row.addStretch(1)
        config_row.addWidget(self.acoustid_autocorrect_checkbox)
        outer.addLayout(config_row)

        actions_row = QHBoxLayout()
        self.checkonly_button = QPushButton("Check AcoustID Only")
        self.checkonly_button.setEnabled(False)
        self.checkonly_button.clicked.connect(self._start_acoustid_check_only)

        self.checkonly_mp3_button = QPushButton("Check AcoustID (incl. MP3)")
        self.checkonly_mp3_button.setToolTip(
            "Scans this folder for both .flac and .mp3 files and runs the AcoustID check on\n"
            "all of them. Only affects this button: Start and Check AcoustID Only still only\n"
            "see .flac files. Files are never converted, and existing tags are never rewritten -\n"
            "the one exception is a missing release-type tag, which gets filled in when\n"
            "Auto-correct is checked (see its tooltip)."
        )
        self.checkonly_mp3_button.setEnabled(False)
        self.checkonly_mp3_button.clicked.connect(self._start_mp3_acoustid_check)

        actions_row.addStretch(1)
        actions_row.addWidget(self.checkonly_button)
        actions_row.addWidget(self.checkonly_mp3_button)
        outer.addLayout(actions_row)
        return outer

    def _save_acoustid_settings(self) -> None:
        self.settings.setValue("acoustid_enabled", self.acoustid_checkbox.isChecked())
        self.settings.setValue("acoustid_autocorrect", self.acoustid_autocorrect_checkbox.isChecked())

    def _build_lidarr_row(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self.lidarr_autoimport_checkbox = QCheckBox("Auto-import to Lidarr after conversion")
        self.lidarr_autoimport_checkbox.setToolTip(
            "When Start finishes converting at least one file, automatically run the same\n"
            "thing Import to Lidarr does - no extra click needed. Off by default. Only runs\n"
            "after a real conversion (not a Check AcoustID Only/+MP3 run), and only if a URL\n"
            "and API key are set in Settings...; otherwise it's silently skipped."
        )
        self.lidarr_autoimport_checkbox.setChecked(self.settings.value("lidarr_autoimport", False, type=bool))
        self.lidarr_autoimport_checkbox.toggled.connect(
            lambda checked: self.settings.setValue("lidarr_autoimport", checked)
        )

        settings_button = QPushButton("Settings...")
        settings_button.clicked.connect(self._open_lidarr_settings)

        self.lidarr_import_button = QPushButton("Import to Lidarr")
        self.lidarr_import_button.setToolTip(
            "Entirely optional and independent of everything else in this window: hands the\n"
            "current folder to Lidarr's own Manual Import API (the same logic behind its\n"
            "Manual Import screen), so Lidarr matches, moves, and renames files itself\n"
            "instead of a direct database write. Only files Lidarr can fully auto-match\n"
            "(from embedded tags) are imported; anything it can't match is left alone and\n"
            "reported back, not touched.\n"
            "Configure the URL and API key via Settings..."
        )
        self.lidarr_import_button.setEnabled(False)
        self.lidarr_import_button.clicked.connect(self._start_lidarr_import)

        self.lidarr_force_reimport_button = QPushButton("Force Reimport...")
        self.lidarr_force_reimport_button.setToolTip(
            "Like Import to Lidarr, but also reimports files Lidarr already has a track file\n"
            "record for (a plain import always skips those) - useful after correcting tags on\n"
            "files Lidarr already matched wrong. Lidarr's only way to drop a track file record\n"
            "always deletes the file too, so nothing is deleted here: each already-tracked file\n"
            "is moved aside, Lidarr's own safe cleanup drops the now-genuinely-missing record,\n"
            "then the file is moved straight back before reimporting. Shows a dry-run preview\n"
            "of exactly which files are in scope before anything actually happens."
        )
        self.lidarr_force_reimport_button.setEnabled(False)
        self.lidarr_force_reimport_button.clicked.connect(self._start_lidarr_force_reimport)

        row.addWidget(self.lidarr_autoimport_checkbox)
        row.addStretch(1)
        row.addWidget(settings_button)
        row.addWidget(self.lidarr_import_button)
        row.addWidget(self.lidarr_force_reimport_button)
        return row

    def _open_lidarr_settings(self) -> None:
        LidarrSettingsDialog(self.settings, self).exec()

    def _save_quality_setting(self) -> None:
        self.settings.setValue("quality", self.quality_combo.currentData())

    def _last_folder(self) -> Path | None:
        last = self.settings.value("last_folder")
        if last and Path(last).is_dir():
            return Path(last)
        return None

    def _browse_folder(self) -> None:
        start_dir = self.folder_edit.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Select Music Folder", start_dir)
        if chosen:
            self._set_folder(Path(chosen))

    def _set_folder(self, folder: Path) -> None:
        self.folder_edit.setText(str(folder))
        self.settings.setValue("last_folder", str(folder))
        self.files = core.find_flac_files(folder)
        self._populate_table(self.files)
        self.start_button.setEnabled(bool(self.files))
        self.checkonly_button.setEnabled(bool(self.files))
        self.checkonly_mp3_button.setEnabled(True)
        self.lidarr_import_button.setEnabled(True)
        self.lidarr_force_reimport_button.setEnabled(True)
        self.library_stats_button.setEnabled(True)
        self.sort_declutter_button.setEnabled(True)
        if self.files:
            self.status_label.setText(f"{len(self.files)} FLAC file(s) found under {folder}")
        else:
            self.status_label.setText(f"No .flac files found under {folder}")

    def _populate_table(self, files: list[Path]) -> None:
        self.table.setRowCount(len(files))
        for row, path in enumerate(files):
            self.table.setItem(row, 0, QTableWidgetItem(path.name))
            self.table.setItem(row, 1, QTableWidgetItem(STATUS_COLUMN_LABELS["pending"]))
            bar = QProgressBar()
            bar.setRange(0, 100)
            self.table.setCellWidget(row, 2, bar)
            self.table.setItem(row, 3, QTableWidgetItem("-"))
        self.overall_bar.setRange(0, max(1, len(files)))
        self.overall_bar.setValue(0)

    def _require_acoustid_apikey(self) -> str | None:
        if not core.check_fpcalc():
            QMessageBox.critical(self, "AcoustID", "The AcoustID check needs fpcalc (chromaprint) on PATH.")
            return None
        apikey = self.settings.value("acoustid_api_key", "").strip()
        if not apikey:
            QMessageBox.critical(self, "AcoustID", "The AcoustID check needs an API key, set via Settings...")
            return None
        return apikey

    def _start_conversion(self) -> None:
        acoustid_apikey = None
        if self.acoustid_checkbox.isChecked():
            acoustid_apikey = self._require_acoustid_apikey()
            if acoustid_apikey is None:
                return
        self._run_batch(self.files, acoustid_apikey, acoustid_only=False, log_prefix="acoustid-convert")

    def _start_acoustid_check_only(self) -> None:
        acoustid_apikey = self._require_acoustid_apikey()
        if acoustid_apikey is None:
            return
        self._run_batch(self.files, acoustid_apikey, acoustid_only=True, log_prefix="acoustid-check")

    def _start_mp3_acoustid_check(self) -> None:
        acoustid_apikey = self._require_acoustid_apikey()
        if acoustid_apikey is None:
            return
        folder = Path(self.folder_edit.text())
        files = core.find_flac_and_mp3_files(folder)
        if not files:
            QMessageBox.information(self, "AcoustID", f"No .flac or .mp3 files found under {folder}")
            return
        # Deliberately not stored on self.files: this scan (and its table
        # listing) is scoped to this button only, so Start and Check
        # AcoustID Only keep targeting the folder's FLAC files afterwards.
        self._run_batch(files, acoustid_apikey, acoustid_only=True, log_prefix="acoustid-check-mp3")

    def _start_lidarr_import(self) -> None:
        self._run_lidarr_import(force=False)

    def _start_lidarr_force_reimport(self) -> None:
        base_url = self.settings.value("lidarr_url", "")
        api_key = self.settings.value("lidarr_api_key", "")
        if not base_url or not api_key:
            QMessageBox.critical(
                self, "AcoustID", "Set the Lidarr URL and API key first, via Settings..."
            )
            return

        folder = Path(self.folder_edit.text())
        local_root = self.settings.value("lidarr_local_root", "")
        lidarr_root = self.settings.value("lidarr_root", "")
        self.lidarr_force_reimport_button.setEnabled(False)
        self.status_label.setText(f"Checking what Force Reimport would do under {folder}...")

        self.lidarr_force_reimport_plan_worker = LidarrForceReimportPlanWorker(
            base_url, api_key, folder, local_root, lidarr_root
        )
        self.lidarr_force_reimport_plan_worker.plan_finished.connect(self._on_force_reimport_plan_finished)
        self.lidarr_force_reimport_plan_worker.plan_error.connect(self._on_force_reimport_plan_error)
        self.lidarr_force_reimport_plan_worker.start()

    def _on_force_reimport_plan_finished(self, in_scope: list[tuple[dict, Path]]) -> None:
        self.lidarr_force_reimport_button.setEnabled(True)
        self.status_label.setText(f"Force Reimport preview: {len(in_scope)} tracked file(s) in scope")
        folder = Path(self.folder_edit.text())
        dialog = ForceReimportPreviewDialog(in_scope, folder, self)
        if dialog.exec() == QDialog.Accepted:
            self._run_lidarr_import(force=True)

    def _on_force_reimport_plan_error(self, message: str) -> None:
        self.lidarr_force_reimport_button.setEnabled(True)
        self.status_label.setText(f"Force Reimport preview failed: {message}")
        QMessageBox.critical(self, "AcoustID", message)

    def _run_lidarr_import(self, force: bool) -> None:
        base_url = self.settings.value("lidarr_url", "")
        api_key = self.settings.value("lidarr_api_key", "")
        if not base_url or not api_key:
            QMessageBox.critical(
                self, "AcoustID", "Set the Lidarr URL and API key first, via Settings..."
            )
            return

        folder = Path(self.folder_edit.text())
        local_root = self.settings.value("lidarr_local_root", "")
        lidarr_root = self.settings.value("lidarr_root", "")
        self.lidarr_import_button.setEnabled(False)
        self.lidarr_force_reimport_button.setEnabled(False)
        verb = "Force-reimporting" if force else "Handing"
        self.status_label.setText(f"{verb} {folder} {'via' if force else 'to'} Lidarr's Manual Import API...")

        if self.lidarr_log_window is None:
            self.lidarr_log_window = LidarrImportLogWindow(self)
        self.lidarr_log_window.reset(f"Lidarr {'force reimport' if force else 'import'} - {folder.name}")
        self.lidarr_log_window.show()
        self.lidarr_log_window.raise_()

        self.lidarr_worker = LidarrImportWorker(base_url, api_key, folder, local_root, lidarr_root, force=force)
        self.lidarr_worker.import_progress.connect(self.lidarr_log_window.append_line)
        self.lidarr_worker.import_finished.connect(self._on_lidarr_import_finished)
        self.lidarr_worker.import_error.connect(self._on_lidarr_import_error)
        self.lidarr_worker.start()

    def _on_lidarr_import_finished(self, imported: int, skipped: int, skipped_names: str) -> None:
        self.lidarr_import_button.setEnabled(True)
        self.lidarr_force_reimport_button.setEnabled(True)
        self.status_label.setText(f"Lidarr import: {imported} imported, {skipped} skipped")
        message = f"Lidarr imported {imported} file(s)."
        if skipped:
            message += f"\n\n{skipped} file(s) Lidarr couldn't auto-match were left untouched:\n{skipped_names}"
        QMessageBox.information(self, "Lidarr import", message)

    def _on_lidarr_import_error(self, message: str) -> None:
        self.lidarr_import_button.setEnabled(True)
        self.lidarr_force_reimport_button.setEnabled(True)
        self.status_label.setText(f"Lidarr import failed: {message}")
        if self.lidarr_log_window is not None:
            self.lidarr_log_window.append_line(f"FAILED: {message}")
        QMessageBox.critical(self, "Lidarr import failed", message)

    def _start_library_stats_scan(self) -> None:
        folder = Path(self.folder_edit.text())
        self.library_stats_button.setEnabled(False)
        self.status_label.setText(f"Scanning {folder} for release types...")

        self.library_stats_worker = LibraryStatsWorker(folder)
        self.library_stats_worker.scan_finished.connect(self._on_library_stats_finished)
        self.library_stats_worker.scan_error.connect(self._on_library_stats_error)
        self.library_stats_worker.start()

    def _on_library_stats_finished(self, type_counts: Counter[str], provenance_counts: Counter[str]) -> None:
        self.library_stats_button.setEnabled(True)
        total = sum(type_counts.values())
        self.status_label.setText(f"Collection summary: {total} release(s)")
        folder = Path(self.folder_edit.text())
        self.library_stats_window = LibraryStatsWindow(type_counts, provenance_counts, folder, self)
        self.library_stats_window.show()

    def _on_library_stats_error(self, message: str) -> None:
        self.library_stats_button.setEnabled(True)
        self.status_label.setText(f"Collection summary failed: {message}")
        QMessageBox.critical(self, "AcoustID", message)

    def _start_declutter_scan(self) -> None:
        folder = Path(self.folder_edit.text())
        self.sort_declutter_button.setEnabled(False)
        self.status_label.setText(f"Scanning {folder} for reissues/compilations...")

        self.declutter_scan_worker = DeclutterScanWorker(folder)
        self.declutter_scan_worker.scan_finished.connect(self._on_declutter_scan_finished)
        self.declutter_scan_worker.scan_error.connect(self._on_declutter_scan_error)
        self.declutter_scan_worker.start()

    def _on_declutter_scan_finished(self, moves: list[library_stats.DeclutterMove]) -> None:
        self.sort_declutter_button.setEnabled(True)
        self.status_label.setText(f"Reissue/compilation scan complete: {len(moves)} candidate(s)")
        folder = Path(self.folder_edit.text())
        self.declutter_sort_dialog = DeclutterSortDialog(moves, folder, self)
        self.declutter_sort_dialog.show()

    def _on_declutter_scan_error(self, message: str) -> None:
        self.sort_declutter_button.setEnabled(True)
        self.status_label.setText(f"Reissue/compilation scan failed: {message}")
        QMessageBox.critical(self, "AcoustID", message)

    def _maybe_autoimport_to_lidarr(self) -> None:
        """Called after a real conversion finishes. Unlike the manual
        Import to Lidarr button, an unconfigured URL/key here is not an
        error worth interrupting the user over - they just haven't set up
        Lidarr, or don't want auto-import right now despite the checkbox;
        silently do nothing rather than popping a dialog."""
        if not self.lidarr_autoimport_checkbox.isChecked():
            return
        if not self.settings.value("lidarr_url", "") or not self.settings.value("lidarr_api_key", ""):
            return
        self._start_lidarr_import()

    def _run_batch(
        self, files: list[Path], acoustid_apikey: str | None, acoustid_only: bool, log_prefix: str
    ) -> None:
        folder = Path(self.folder_edit.text())
        quality = self.quality_combo.currentData()
        log_path = folder / f"{log_prefix}-{datetime.now():%Y%m%d-%H%M%S}.log"
        acoustid_autocorrect = self.acoustid_autocorrect_checkbox.isChecked()

        self._acoustid_only_run = acoustid_only
        self._populate_table(files)
        self.start_button.setEnabled(False)
        self.checkonly_button.setEnabled(False)
        self.checkonly_mp3_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._completed = 0

        self.converter = BatchConverter(
            files,
            quality,
            log_path,
            self.workers_spin.value(),
            acoustid_apikey,
            acoustid_only,
            acoustid_autocorrect,
        )
        self.converter.file_started.connect(self._on_file_started)
        self.converter.file_progress.connect(self._on_file_progress)
        self.converter.file_finished.connect(self._on_file_finished)
        self.converter.acoustid_checked.connect(self._on_acoustid_checked)
        self.converter.batch_finished.connect(self._on_batch_finished)
        self.converter.batch_error.connect(self._on_batch_error)
        self.converter.start()

    def _cancel_conversion(self) -> None:
        if self.converter is not None:
            self.converter.cancel()
        self.cancel_button.setEnabled(False)
        self.status_label.setText("Cancelling after in-flight files finish...")

    def _on_file_started(self, row: int) -> None:
        label = "checking" if self._acoustid_only_run else "running"
        self.table.item(row, 1).setText(STATUS_COLUMN_LABELS[label])

    def _on_file_progress(self, row: int, percent: float, speed: str) -> None:
        bar = self.table.cellWidget(row, 2)
        if isinstance(bar, QProgressBar):
            bar.setValue(int(percent))
            bar.setFormat(f"{percent:.0f}%  {speed}")

    def _on_acoustid_checked(self, row: int, status: str, detail: str, corrected: bool) -> None:
        item = self.table.item(row, 3)
        label = ACOUSTID_STATUS_LABELS.get(status, status)
        item.setText(f"{label} (fixed)" if corrected else label)
        item.setToolTip(detail)

    def _on_file_finished(self, row: int, ok: bool) -> None:
        if self._acoustid_only_run:
            label = "checked"
        else:
            label = "ok" if ok else "fail"
        self.table.item(row, 1).setText(STATUS_COLUMN_LABELS[label])
        self._completed += 1
        self.overall_bar.setValue(self._completed)

    def _on_batch_finished(
        self, ok_count: int, fail_count: int, log_path: str, src_bytes: int, dst_bytes: int
    ) -> None:
        self.cancel_button.setEnabled(False)
        # This rescans the folder fresh each time it's clicked, so it stays
        # usable regardless of what Start/Check-Only just did to self.files.
        self.checkonly_mp3_button.setEnabled(bool(self.folder_edit.text()))

        if self._acoustid_only_run:
            # Files are untouched by a check-only run, so both actions stay
            # available for a follow-up conversion.
            self.start_button.setEnabled(bool(self.files))
            self.checkonly_button.setEnabled(bool(self.files))
            self.status_label.setText(f"AcoustID check complete: {ok_count} checked, {fail_count} errored")
            return

        # Leave the table as-is (per-file Done/Failed results) so the run can
        # be reviewed; Browse a folder again to start a new batch. Start
        # stays disabled since self.files now points at already-converted
        # (deleted) sources.
        self.start_button.setEnabled(False)
        self.checkonly_button.setEnabled(False)
        self.status_label.setText(f"Converted: {ok_count}  Failed: {fail_count}  Log: {log_path}")

        if ok_count:
            saved = src_bytes - dst_bytes
            percent = (saved / src_bytes * 100) if src_bytes else 0.0
            QMessageBox.information(
                self,
                "Conversion complete",
                f"Converted {ok_count} file(s) ({fail_count} failed)\n\n"
                f"Before: {_format_bytes(src_bytes)}\n"
                f"After:  {_format_bytes(dst_bytes)}\n"
                f"Saved:  {_format_bytes(saved)} ({percent:.0f}%)",
            )
            self._maybe_autoimport_to_lidarr()

    def _on_batch_error(self, message: str) -> None:
        self.start_button.setEnabled(bool(self.files))
        self.checkonly_button.setEnabled(bool(self.files))
        self.checkonly_mp3_button.setEnabled(bool(self.folder_edit.text()))
        self.cancel_button.setEnabled(False)
        self.status_label.setText(message)
        QMessageBox.critical(self, "Conversion failed", message)


def main() -> None:
    if lidarr_hook.is_invocation():
        # Lidarr Custom Script invocation: no display available in general
        # (this may run inside Lidarr's own container), so never touch
        # QApplication/Qt widgets on this path - just the plain script logic.
        sys.exit(lidarr_hook.run_from_environment())

    app = QApplication(sys.argv)
    icon_path = Path(__file__).parent / "icons" / "acoustid_256.png"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    if not core.check_ffmpeg():
        QMessageBox.critical(None, "AcoustID", "ffmpeg is required but was not found on PATH.")
        sys.exit(1)

    initial_folder = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    window = MainWindow(initial_folder)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
