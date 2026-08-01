#!/usr/bin/env python3
"""AcoustID: a KDE/Qt window to recursively transcode a folder of FLAC files
to MP3, with an optional AcoustID/MusicBrainz identity check."""

from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import TextIO

from PySide6.QtCore import QSettings, Qt, QThread, Signal
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

__version__ = "0.4"

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
        # Never auto-correct on a check-only run: "Check AcoustID Only"
        # promises files stay untouched, so this flag is ignored there.
        self.acoustid_autocorrect = acoustid_autocorrect and not acoustid_only
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def _check_acoustid(self, index: int, path: Path, log) -> core.AcoustIDCheck:
        result = core.check_acoustid(path, self.acoustid_apikey)
        if self.acoustid_autocorrect:
            core.correct_acoustid_mismatch(path, result)
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
            futures = [pool.submit(self._convert, i, f, log) for i, f in enumerate(self.files)]
            results = [f.result() for f in futures]

        ok_count = sum(r.ok for r in results)
        fail_count = len(results) - ok_count
        src_bytes = sum(r.src_bytes for r in results if r.ok)
        dst_bytes = sum(r.dst_bytes for r in results if r.ok)
        self.batch_finished.emit(ok_count, fail_count, str(self.log_path), src_bytes, dst_bytes)


class LidarrImportWorker(QThread):
    """Hands a folder to Lidarr's Manual Import API in the background so the
    UI doesn't block on the network round-trip. Entirely independent of
    BatchConverter/self.files - it just tells Lidarr which folder to scan."""

    import_finished = Signal(int, int, str)  # imported_count, skipped_count, "; "-joined skipped names
    import_error = Signal(str)

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
            imported, skipped, skipped_names = lidarr.import_folder(
                self.base_url, self.api_key, self.folder, local_root=self.local_root, lidarr_root=self.lidarr_root
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


class LidarrSettingsDialog(QDialog):
    """Modal dialog for the Lidarr URL/API key, with a Test Connection
    button so mistakes (wrong host/port, bad key) show up here instead of
    only surfacing later as a confusing Import to Lidarr failure."""

    def __init__(self, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Lidarr Settings")
        self.settings = settings
        self.test_worker: LidarrConnectionTestWorker | None = None

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.url_edit = QLineEdit(self.settings.value("lidarr_url", ""))
        self.url_edit.setPlaceholderText("http://localhost:8686")
        self.key_edit = QLineEdit(self.settings.value("lidarr_api_key", ""))
        self.key_edit.setPlaceholderText("API key (Lidarr: Settings > General)")
        form.addRow("URL:", self.url_edit)
        form.addRow("API key:", self.key_edit)

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
        self.settings.setValue("lidarr_url", self.url_edit.text().strip())
        self.settings.setValue("lidarr_api_key", self.key_edit.text().strip())
        self.settings.setValue("lidarr_local_root", self.local_root_edit.text().strip())
        self.settings.setValue("lidarr_root", self.lidarr_root_edit.text().strip())
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self, initial_folder: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"AcoustID v{__version__}")
        self.resize(760, 480)

        self.files: list[Path] = []
        self.converter: BatchConverter | None = None
        self.lidarr_worker: LidarrImportWorker | None = None
        self.settings = QSettings("AcoustID", "AcoustID")
        self._acoustid_only_run = False

        self._build_ui()
        folder = initial_folder or self._last_folder()
        if folder is not None:
            self._set_folder(folder)

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
        row.addWidget(QLabel("Folder:"))
        row.addWidget(self.folder_edit, stretch=1)
        row.addWidget(browse_button)
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

        self.start_button = QPushButton("Start")
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

        self.acoustid_key_edit = QLineEdit()
        self.acoustid_key_edit.setPlaceholderText("AcoustID API key (get one at acoustid.org)")
        self.acoustid_key_edit.setText(self.settings.value("acoustid_api_key", ""))
        self.acoustid_key_edit.editingFinished.connect(self._save_acoustid_settings)

        self.acoustid_autocorrect_checkbox = QCheckBox("Auto-correct mismatched MBID")
        self.acoustid_autocorrect_checkbox.setToolTip(
            "When Check AcoustID finds a mismatch with a confident enough score "
            f"(>= {core.ACOUSTID_AUTOCORRECT_MIN_SCORE:.1f}), rewrite the FLAC's "
            "musicbrainz_trackid tag to AcoustID's suggested recording before converting.\n"
            "Never applies during a Check AcoustID Only (or +MP3) run - those always leave files untouched."
        )
        self.acoustid_autocorrect_checkbox.setChecked(self.settings.value("acoustid_autocorrect", False, type=bool))
        self.acoustid_autocorrect_checkbox.toggled.connect(self._save_acoustid_settings)

        config_row.addWidget(self.acoustid_checkbox)
        config_row.addWidget(self.acoustid_key_edit, stretch=1)
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
            "see .flac files. Files are never converted or modified by this."
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
        self.settings.setValue("acoustid_api_key", self.acoustid_key_edit.text().strip())
        self.settings.setValue("acoustid_autocorrect", self.acoustid_autocorrect_checkbox.isChecked())

    def _build_lidarr_row(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self.lidarr_autoimport_checkbox = QCheckBox("Auto-import to Lidarr after conversion")
        self.lidarr_autoimport_checkbox.setToolTip(
            "When Start finishes converting at least one file, automatically run the same\n"
            "thing Import to Lidarr does - no extra click needed. Off by default. Only runs\n"
            "after a real conversion (not a Check AcoustID Only/+MP3 run), and only if a URL\n"
            "and API key are set in Lidarr Settings...; otherwise it's silently skipped."
        )
        self.lidarr_autoimport_checkbox.setChecked(self.settings.value("lidarr_autoimport", False, type=bool))
        self.lidarr_autoimport_checkbox.toggled.connect(
            lambda checked: self.settings.setValue("lidarr_autoimport", checked)
        )

        lidarr_settings_button = QPushButton("Lidarr Settings...")
        lidarr_settings_button.clicked.connect(self._open_lidarr_settings)

        self.lidarr_import_button = QPushButton("Import to Lidarr")
        self.lidarr_import_button.setToolTip(
            "Entirely optional and independent of everything else in this window: hands the\n"
            "current folder to Lidarr's own Manual Import API (the same logic behind its\n"
            "Manual Import screen), so Lidarr matches, moves, and renames files itself\n"
            "instead of a direct database write. Only files Lidarr can fully auto-match\n"
            "(from embedded tags) are imported; anything it can't match is left alone and\n"
            "reported back, not touched.\n"
            "Configure the URL and API key via Lidarr Settings..."
        )
        self.lidarr_import_button.setEnabled(False)
        self.lidarr_import_button.clicked.connect(self._start_lidarr_import)

        row.addWidget(self.lidarr_autoimport_checkbox)
        row.addStretch(1)
        row.addWidget(lidarr_settings_button)
        row.addWidget(self.lidarr_import_button)
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
        apikey = self.acoustid_key_edit.text().strip()
        if not apikey:
            QMessageBox.critical(self, "AcoustID", "The AcoustID check needs an API key.")
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
        base_url = self.settings.value("lidarr_url", "")
        api_key = self.settings.value("lidarr_api_key", "")
        if not base_url or not api_key:
            QMessageBox.critical(
                self, "AcoustID", "Set the Lidarr URL and API key first, via Lidarr Settings..."
            )
            return

        folder = Path(self.folder_edit.text())
        local_root = self.settings.value("lidarr_local_root", "")
        lidarr_root = self.settings.value("lidarr_root", "")
        self.lidarr_import_button.setEnabled(False)
        self.status_label.setText(f"Handing {folder} to Lidarr's Manual Import API...")

        self.lidarr_worker = LidarrImportWorker(base_url, api_key, folder, local_root, lidarr_root)
        self.lidarr_worker.import_finished.connect(self._on_lidarr_import_finished)
        self.lidarr_worker.import_error.connect(self._on_lidarr_import_error)
        self.lidarr_worker.start()

    def _on_lidarr_import_finished(self, imported: int, skipped: int, skipped_names: str) -> None:
        self.lidarr_import_button.setEnabled(True)
        self.status_label.setText(f"Lidarr import: {imported} imported, {skipped} skipped")
        message = f"Lidarr imported {imported} file(s)."
        if skipped:
            message += f"\n\n{skipped} file(s) Lidarr couldn't auto-match were left untouched:\n{skipped_names}"
        QMessageBox.information(self, "Lidarr import", message)

    def _on_lidarr_import_error(self, message: str) -> None:
        self.lidarr_import_button.setEnabled(True)
        self.status_label.setText(f"Lidarr import failed: {message}")
        QMessageBox.critical(self, "Lidarr import failed", message)

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
    if not core.check_ffmpeg():
        QMessageBox.critical(None, "AcoustID", "ffmpeg is required but was not found on PATH.")
        sys.exit(1)

    initial_folder = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    window = MainWindow(initial_folder)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
