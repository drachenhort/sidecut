---
name: run-flac2mp3
description: Build, run, and drive flac2mp3's sidecut.py PySide6 desktop app headlessly. Use when asked to start/run/launch flac2mp3, take a screenshot of its UI, click a button or interact with a widget, or confirm a UI change works in the real app (not just pytest).
---

flac2mp3's UI is `sidecut.py`, a PySide6/Qt desktop app. It runs headless
in this container via Qt's `offscreen` QPA platform (no X server, no xvfb,
no window manager needed — `offscreen` renders straight into memory). Drive
it with `.claude/skills/run-flac2mp3/driver.py`: pipe it newline-delimited
commands on stdin, it launches the real `MainWindow`, executes each command
against it (click a button, set a field, wait for a background thread,
grab a screenshot), and prints one `OK`/`ERR` reply line per command.

All paths below are relative to `flac2mp3/` (this project's root — it's a
subdirectory of a larger monorepo, not its own git repo).

## Prerequisites

No OS packages needed beyond what's already in this container — the
`offscreen` platform plugin worked with zero missing-library errors on
first run. If you see a Qt plugin-loading error, it means the container
changed; there's no `apt-get` line to fall back to, diagnose from the
actual error.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

## Build

No build step — it's a plain Python script.

## Run (agent path)

Pipe a command script into the driver's stdin. Each invocation launches a
fresh `QApplication` + `MainWindow`, runs your commands against it in
order, and exits — there is no persistent session to attach to across
separate tool calls (this container has no `tmux`), so a "session" is one
heredoc covering everything you need from that launch.

**Isolate settings first.** `sidecut.py` uses
`QSettings("AcoustID", "AcoustID")`, which on Linux persists to
`$HOME/.config/AcoustID/AcoustID.conf`. Point `HOME` at a scratch
directory or you'll read (and can overwrite) the real user's last-used
folder, quality setting, and Lidarr credentials:

```bash
FAKEHOME=/tmp/sidecut-driver-home
mkdir -p "$FAKEHOME"
cat <<'EOF' | HOME="$FAKEHOME" QT_QPA_PLATFORM=offscreen .venv/bin/python3 .claude/skills/run-flac2mp3/driver.py
screenshot /tmp/shots/01-initial.png
quit
EOF
```

Must run with cwd = `flac2mp3/` (the driver adds cwd to `sys.path` itself
to find `sidecut.py`, but only if cwd is right when it starts).

Screenshots land wherever you point the `screenshot` command's path —
use an absolute path, e.g. under `/tmp/shots/`.

### Driver commands

| command | what it does |
|---|---|
| `click <attr>` | `QTest.mouseClick` on `window.<attr>` (e.g. `click library_stats_button`) |
| `check <attr> 0\|1` | Set a `QCheckBox`'s checked state via a real click (fires signals) |
| `settext <attr> <text>` | `setText` on a `QLineEdit` — does **not** fire the app's own logic, see Gotchas |
| `select <attr> <index>` | `setCurrentIndex` on a `QComboBox` |
| `screenshot <path>` | `window.grab().save(path)` — PNG of the main window |
| `wait <ms>` | Pump the Qt event loop for `<ms>` milliseconds |
| `eval <expr>` | `eval(expr, {"window": window, "sidecut": sidecut})`, prints `repr()` — escape hatch for anything not covered above |
| `quit` | Exit cleanly |

Widget names are the same attribute names used in `sidecut.py`'s
`MainWindow` (e.g. `folder_edit`, `library_stats_button`,
`sort_declutter_button`, `quality_combo`, `workers_spin`, `start_button`,
`cancel_button`, `acoustid_checkbox`, `acoustid_autocorrect_checkbox`,
`lidarr_autoimport_checkbox`, `lidarr_import_button`,
`lidarr_force_reimport_button`, `checkonly_button`,
`checkonly_mp3_button`) — grep `sidecut.py` for `self\.\w* = Q` to find
more, including inside dialogs (`LidarrSettingsDialog`'s
`url_edit`/`key_edit`/`test_button`, etc. — reach those via `eval` since
they're not `MainWindow` attributes, e.g.
`eval window._open_lidarr_settings() or window.findChild(sidecut.QDialog)`).

### Worked example: set a folder, run the Collection Summary scan, screenshot it

```bash
FAKEHOME=/tmp/sidecut-driver-home
mkdir -p "$FAKEHOME"
cat <<'EOF' | HOME="$FAKEHOME" QT_QPA_PLATFORM=offscreen .venv/bin/python3 .claude/skills/run-flac2mp3/driver.py
eval window._set_folder(sidecut.Path("/home/sigma/git/flac2mp3/test-flac"))
wait 300
click library_stats_button
eval window.library_stats_worker.wait(8000)
wait 100
eval window.library_stats_window.grab().save("/tmp/shots/collection-summary.png")
quit
EOF
```

This actually ran in this container: it scanned `test-flac/` (2 releases),
opened the `LibraryStatsWindow` dialog with two live `QChart` bar charts,
and saved a real screenshot of it.

### Worked example: the Lidarr Queue window (auto-opens on startup)

The Lidarr Queue window (`LidarrQueueWindow`) auto-opens in
`MainWindow.__init__` if `lidarr_url`/`lidarr_api_key` are already set in
`QSettings` — so seed the fake home's ini file *before* launching:

```bash
FAKEHOME=/tmp/sidecut-driver-home2
mkdir -p "$FAKEHOME/.config/AcoustID"
cat > "$FAKEHOME/.config/AcoustID/AcoustID.conf" <<'EOF'
[General]
lidarr_url=http://your-lidarr-host:8686
lidarr_api_key=your-key
EOF
cat <<'EOF' | HOME="$FAKEHOME" QT_QPA_PLATFORM=offscreen .venv/bin/python3 .claude/skills/run-flac2mp3/driver.py
eval window.queue_window.worker.wait(8000) if window.queue_window and window.queue_window.worker else "no worker"
wait 100
eval window.queue_window.status_label.text() if window.queue_window else "no window"
eval window.queue_window.grab().save("/tmp/shots/lidarr-queue.png") if window.queue_window else "no window"
quit
EOF
```

Verified against a real Lidarr instance from inside this container: with
a valid URL and a bad API key the window rendered the 401 error inline in
its status label (`Failed to fetch the Lidarr queue: 401 Client Error:
Unauthorized...`), table stayed empty, no crash.

## Run (human path)

```bash
.venv/bin/python3 sidecut.py                  # opens a real window, remembers the last folder
.venv/bin/python3 sidecut.py /path/to/music    # opens with the folder pre-filled
```

Needs a real display (or `QT_QPA_PLATFORM=offscreen`, in which case
nothing is visible — use the driver instead). Ctrl-C or close the window
to stop.

## Test

```bash
.venv/bin/python3 -m pytest -q
```

148 tests pass, ~11s.

---

## Gotchas

- **`settext` on `folder_edit` does not trigger a folder scan.** The
  Browse button and drag-and-drop both call `MainWindow._set_folder(path)`
  internally, which scans for FLAC files and enables/disables buttons
  accordingly (`library_stats_button`, `start_button`, etc. all start
  disabled). Setting the text field directly leaves those buttons
  disabled and `click`ing them does nothing. Use
  `eval window._set_folder(sidecut.Path("/abs/path"))` instead.
- **Background `QThread` work needs `worker.wait(timeout_ms)`, not a
  guessed `wait <ms>`.** Several buttons kick off a `QThread`
  (`LibraryStatsWorker`, `LidarrQueueWorker`, `DeclutterScanWorker`, ...)
  and only build/populate a dialog when it finishes. A fixed `wait 500`
  raced the thread and produced an empty result in testing; blocking on
  the actual worker via `eval window.<worker_attr>.wait(8000)` is
  reliable and as fast as the real work takes.
- **`worker.wait()` returning `True` does not mean the result is on
  screen yet.** `wait()` blocks until the `QThread`'s `run()` returns, but
  the `finished`/`scan_finished` signal it emits is delivered to the main
  thread's slot (which builds the dialog) through the Qt event loop, not
  synchronously. Follow every `worker.wait(...)` with a short `wait 100`
  (event-loop pump) before touching whatever the signal handler was
  supposed to create — skipping this produced `window.library_stats_window
  is None` even though the worker had genuinely finished.
- **`python3 path/to/driver.py` does not add cwd to `sys.path`** — only
  the driver's own directory. The driver inserts `os.getcwd()` into
  `sys.path` itself at the top, but that only works if cwd is
  `flac2mp3/` when you launch it (`import sidecut` fails otherwise).
- **No `tmux` in this container.** The driver is a straight stdin/stdout
  REPL instead of a background-and-attach pattern — one heredoc per
  "session," which is fine since each launch is fast (~1-2s) and the app
  doesn't need cross-invocation state for anything the pytest suite
  doesn't already cover.
- **Quitting the process while a `QThread` is still running aborts it**
  (`QThread: Destroyed while thread is still running` → SIGABRT). This
  is a real, pre-existing behavior of several worker classes in
  `sidecut.py`, not a driver bug — always `wait()` on any worker you
  started before sending `quit`.
- **Real `QSettings` are shared with the actual user's app** if you don't
  set `HOME`. In this container a real Lidarr instance happened to
  already be configured at `~/.config/AcoustID/AcoustID.conf` from prior
  manual use — driving without an isolated `HOME` will read (and can
  overwrite, e.g. `settext` into a settings-backed field) that real
  configuration.

## Troubleshooting

- **`ModuleNotFoundError: No module named 'sidecut'`**: cwd wasn't
  `flac2mp3/` when the driver started. `cd` there (in the same shell
  invocation as the `HOME=... python3 .../driver.py` command — cwd does
  not persist reliably across separate tool calls in this harness).
- **`This plugin does not support propagateSizeHints()` on stderr**:
  harmless — a known `offscreen`-platform limitation, not an error. Every
  successful run in this container printed it.
