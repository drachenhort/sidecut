#!/usr/bin/env python3
"""Headless driver for acoustid.py, flac2mp3's PySide6 desktop app.

Runs the real QApplication with the "offscreen" QPA platform (no X server /
xvfb needed - offscreen renders into memory, which is why this works in a
plain container). Reads newline-delimited commands from stdin and prints one
reply line per command to stdout, flushed immediately so a driving process
(e.g. tmux send-keys + capture-pane) can pace itself.

Commands:
  click <attr>              QTest.mouseClick on window.<attr>
  check <attr> 0|1          setChecked on a QCheckBox, then click to fire signals
  settext <attr> <text...>  setText on a QLineEdit-like widget (rest of line is the text)
  select <attr> <index>     setCurrentIndex on a QComboBox
  screenshot <path>         grab() the main window and save a PNG
  wait <ms>                 pump the Qt event loop for <ms> milliseconds
  eval <expr>               eval(expr, {"window": window, "acoustid": acoustid})
                             and print its repr - escape hatch for anything
                             not covered above (e.g. reading a label's text,
                             checking a QSettings value)
  quit                      exit cleanly

Every reply is exactly one line: "OK <message>" or "ERR <message>".

Isolate QSettings before launching this (acoustid.py uses
QSettings("AcoustID", "AcoustID"), which persists to $HOME/.config on Linux)
by running with a scratch HOME, e.g.:
  HOME=/tmp/acoustid-driver-home QT_QPA_PLATFORM=offscreen python3 driver.py
Otherwise you inherit (and can overwrite) the real user's last-used folder,
quality setting, and Lidarr credentials.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.getcwd())  # python3 path/to/driver.py only adds the driver's own dir, not cwd

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QLineEdit

import acoustid


def reply(ok: bool, message: str) -> None:
    print(f"{'OK' if ok else 'ERR'} {message}", flush=True)


def main() -> None:
    app = QApplication(sys.argv[:1])
    window = acoustid.MainWindow()
    window.resize(760, 480)
    window.show()
    app.processEvents()

    for line in sys.stdin:
        line = line.rstrip("\n")
        if not line.strip():
            continue
        parts = line.split(" ", 2)
        cmd = parts[0]
        try:
            if cmd == "click":
                widget = getattr(window, parts[1])
                QTest.mouseClick(widget, Qt.LeftButton)
                app.processEvents()
                reply(True, f"clicked {parts[1]}")

            elif cmd == "check":
                widget = getattr(window, parts[1])
                if not isinstance(widget, QCheckBox):
                    raise TypeError(f"{parts[1]} is not a QCheckBox")
                want = parts[2] == "1"
                if widget.isChecked() != want:
                    QTest.mouseClick(widget, Qt.LeftButton)
                    app.processEvents()
                reply(True, f"{parts[1]} checked={widget.isChecked()}")

            elif cmd == "settext":
                widget = getattr(window, parts[1])
                if not isinstance(widget, QLineEdit):
                    raise TypeError(f"{parts[1]} is not a QLineEdit")
                widget.setText(parts[2] if len(parts) > 2 else "")
                app.processEvents()
                reply(True, f"{parts[1]} text={widget.text()!r}")

            elif cmd == "select":
                widget = getattr(window, parts[1])
                if not isinstance(widget, QComboBox):
                    raise TypeError(f"{parts[1]} is not a QComboBox")
                widget.setCurrentIndex(int(parts[2]))
                app.processEvents()
                reply(True, f"{parts[1]} index={widget.currentIndex()} text={widget.currentText()!r}")

            elif cmd == "screenshot":
                path = parts[1]
                window.grab().save(path)
                reply(True, f"saved {path}")

            elif cmd == "wait":
                ms = int(parts[1])
                QTest.qWait(ms)
                reply(True, f"waited {ms}ms")

            elif cmd == "eval":
                expr = line[len("eval "):]
                result = eval(expr, {"window": window, "acoustid": acoustid})  # noqa: S307 - trusted local driver input
                reply(True, repr(result))

            elif cmd == "quit":
                reply(True, "bye")
                break

            else:
                reply(False, f"unknown command: {cmd}")

        except Exception as exc:  # noqa: BLE001 - report to the caller, keep the REPL alive
            reply(False, f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
