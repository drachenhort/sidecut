#!/usr/bin/env python3
"""KDE/Qt window to recursively transcode a folder of FLAC files to MP3."""

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
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
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

__version__ = "0.2"

STATUS_COLUMN_LABELS = {"pending": "Pending", "running": "Converting...", "ok": "Done", "fail": "Failed"}


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
    batch_finished = Signal(int, int, str, "qint64", "qint64")
    batch_error = Signal(str)

    def __init__(self, files: list[Path], quality: str, log_path: Path, workers: int) -> None:
        super().__init__()
        self.files = files
        self.quality_args = core.QUALITY_PRESETS[quality]
        self.log_path = log_path
        self.workers = workers
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def _convert(self, index: int, path: Path, log) -> core.ConversionResult:
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
            fallback_dir = Path.home() / ".local" / "share" / "flac2mp3" / "logs"
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


class MainWindow(QMainWindow):
    def __init__(self, initial_folder: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"flac2mp3 v{__version__}")
        self.resize(760, 480)

        self.files: list[Path] = []
        self.converter: BatchConverter | None = None
        self.settings = QSettings("flac2mp3", "flac2mp3")

        self._build_ui()
        if initial_folder is not None:
            self._set_folder(initial_folder)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        layout.addLayout(self._build_folder_row())
        layout.addLayout(self._build_options_row())

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["File", "Status", "Progress"])
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

    def _save_quality_setting(self) -> None:
        self.settings.setValue("quality", self.quality_combo.currentData())

    def _browse_folder(self) -> None:
        start_dir = self.folder_edit.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Select Music Folder", start_dir)
        if chosen:
            self._set_folder(Path(chosen))

    def _set_folder(self, folder: Path) -> None:
        self.folder_edit.setText(str(folder))
        self.files = core.find_flac_files(folder)
        self._populate_table()
        self.start_button.setEnabled(bool(self.files))
        if self.files:
            self.status_label.setText(f"{len(self.files)} FLAC file(s) found under {folder}")
        else:
            self.status_label.setText(f"No .flac files found under {folder}")

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self.files))
        for row, path in enumerate(self.files):
            self.table.setItem(row, 0, QTableWidgetItem(path.name))
            self.table.setItem(row, 1, QTableWidgetItem(STATUS_COLUMN_LABELS["pending"]))
            bar = QProgressBar()
            bar.setRange(0, 100)
            self.table.setCellWidget(row, 2, bar)
        self.overall_bar.setRange(0, max(1, len(self.files)))
        self.overall_bar.setValue(0)

    def _start_conversion(self) -> None:
        folder = Path(self.folder_edit.text())
        quality = self.quality_combo.currentData()
        log_path = folder / f"flac2mp3-{datetime.now():%Y%m%d-%H%M%S}.log"

        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._completed = 0

        self.converter = BatchConverter(self.files, quality, log_path, self.workers_spin.value())
        self.converter.file_started.connect(self._on_file_started)
        self.converter.file_progress.connect(self._on_file_progress)
        self.converter.file_finished.connect(self._on_file_finished)
        self.converter.batch_finished.connect(self._on_batch_finished)
        self.converter.batch_error.connect(self._on_batch_error)
        self.converter.start()

    def _cancel_conversion(self) -> None:
        if self.converter is not None:
            self.converter.cancel()
        self.cancel_button.setEnabled(False)
        self.status_label.setText("Cancelling after in-flight files finish...")

    def _on_file_started(self, row: int) -> None:
        self.table.item(row, 1).setText(STATUS_COLUMN_LABELS["running"])

    def _on_file_progress(self, row: int, percent: float, speed: str) -> None:
        bar = self.table.cellWidget(row, 2)
        if isinstance(bar, QProgressBar):
            bar.setValue(int(percent))
            bar.setFormat(f"{percent:.0f}%  {speed}")

    def _on_file_finished(self, row: int, ok: bool) -> None:
        self.table.item(row, 1).setText(STATUS_COLUMN_LABELS["ok" if ok else "fail"])
        self._completed += 1
        self.overall_bar.setValue(self._completed)

    def _on_batch_finished(
        self, ok_count: int, fail_count: int, log_path: str, src_bytes: int, dst_bytes: int
    ) -> None:
        # Leave the table as-is (per-file Done/Failed results) so the run can
        # be reviewed; Browse a folder again to start a new batch. Start
        # stays disabled since self.files now points at already-converted
        # (deleted) sources.
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
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

    def _on_batch_error(self, message: str) -> None:
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.status_label.setText(message)
        QMessageBox.critical(self, "Conversion failed", message)


def main() -> None:
    app = QApplication(sys.argv)
    if not core.check_ffmpeg():
        QMessageBox.critical(None, "flac2mp3", "ffmpeg is required but was not found on PATH.")
        sys.exit(1)

    initial_folder = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    window = MainWindow(initial_folder)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
