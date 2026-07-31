# flac2mp3

A native Qt/KDE window to recursively transcode a folder of FLAC files to
MP3, in place.

## Requirements

- `ffmpeg` on `PATH`
- Python 3.10+
- `pip install -r requirements.txt` (`mutagen` for tag copying, `PySide6`
  for the GUI — auto-follows your KDE Plasma/Breeze theme)

## Usage

```bash
python3 flac2mp3.py                  # opens the window, folder unset
python3 flac2mp3.py /path/to/music   # opens the window with the folder pre-filled
```

In the window:

1. **Browse...** to pick the folder to scan recursively (native KDE folder
   dialog).
2. Choose a **quality** preset and the number of **parallel jobs**.
3. **Start** — each file gets its own row with a live progress bar
   (percent, encode speed) fed from ffmpeg's `-progress` stream. **Cancel**
   stops queued files immediately and lets in-flight ones finish or abort.

## Behavior

- Scans recursively for `*.flac` (case-insensitive).
- Each file is converted to a `.mp3` next to it. **The original `.flac` is
  only deleted after the `.mp3` is verified** (ffmpeg succeeded and the
  output file is non-empty) and its tags have been copied over. Anything
  that fails is left untouched and recorded in the run's log file.
- All tags are preserved, including non-standard ones such as
  `ACOUSTID_ID`, `ACOUSTID_FINGERPRINT`, `MUSICBRAINZ_*`, and
  `REPLAYGAIN_*` — well-known tags map to standard ID3v2 frames, everything
  else is kept as a `TXXX` frame so nothing is silently dropped.
- Embedded cover art (front/back covers, etc.) is copied as ID3 `APIC`
  frames.
- A log file `flac2mp3-<timestamp>.log` is written in the scanned root
  folder, containing ffmpeg output for any failures.
- Re-running on the same folder is safe: files that already converted no
  longer have a `.flac` source and are skipped automatically, so an
  interrupted run on a large library can simply be restarted.

## Code layout

- `core.py` — framework-agnostic conversion logic (scanning, ffmpeg
  invocation, tag/picture copying); no Qt dependency, directly unit tested.
- `flac2mp3.py` — the PySide6 window and its background conversion thread.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
