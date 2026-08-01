# AcoustID

A native Qt/KDE window to recursively transcode a folder of FLAC files to
MP3, in place, with an optional AcoustID/MusicBrainz identity check.

## Requirements

- `ffmpeg` on `PATH`
- Python 3.10+
- `pip install -r requirements.txt` (`mutagen` for tag copying, `PySide6`
  for the GUI — auto-follows your KDE Plasma/Breeze theme, `requests` for
  the optional AcoustID lookup)
- Optional: `fpcalc` (from `chromaprint`/`libchromaprint-tools`) and a free
  [AcoustID](https://acoustid.org/) API key, only needed if you enable the
  AcoustID check

## Usage

```bash
python3 acoustid.py                  # opens the window, remembers the last folder used
python3 acoustid.py /path/to/music   # opens the window with the folder pre-filled
```

In the window:

1. **Browse...** to pick the folder to scan recursively (native KDE folder
   dialog). The chosen folder is remembered (Qt `QSettings`) and reopened
   automatically next time, unless a folder is passed on the command line.
2. Choose a **quality** preset and the number of **parallel jobs**.
3. Optionally tick **Check AcoustID** and enter an AcoustID API key to
   fingerprint each file and compare it against MusicBrainz before
   converting (see below).
4. **Start** — each file gets its own row with a live progress bar
   (percent, encode speed) fed from ffmpeg's `-progress` stream. **Cancel**
   stops queued files immediately and lets in-flight ones finish or abort.

## AcoustID check (optional)

- Off by default. Tick **Check AcoustID** to run the check as part of a
  normal conversion, or click **Check AcoustID Only** to just fingerprint
  and look up every file (no conversion, files untouched) — handy for
  vetting a library's tags before committing to a batch.
- Either way, each file is fingerprinted with `fpcalc` and looked up via
  the AcoustID web service.
- The result is shown in the **AcoustID** column: **Match** (agrees with
  the file's tagged `MUSICBRAINZ_TRACKID`), **Mismatch** (tagged ID isn't
  among AcoustID's results), **Identified** (no existing tag, but AcoustID
  found a candidate), **No match**, or **Error**. Hover a cell for details —
  on a **Mismatch**, the tooltip spells out both the currently tagged
  artist/title/MBID and what AcoustID says the correct one is.
- By itself this is report-only: it never blocks, retags, or otherwise
  changes the file — it's purely informational, also written to the log
  file.
- Optionally tick **Auto-correct mismatched MBID** to have a **Mismatch**
  fix itself: the FLAC's `musicbrainz_trackid` tag is rewritten to
  AcoustID's suggested recording (only the ID — artist/title/etc. are left
  alone) before the file converts, but only when AcoustID's score is
  >= 0.5. Corrected rows show "Mismatch (fixed)". This never applies
  during a **Check AcoustID Only** (or **+MP3**) run, which always leave
  files untouched regardless of this checkbox.
- Lookups are capped at 4 requests/second total, no matter how many
  parallel jobs are configured — workers block on a shared limiter rather
  than each hammering the API independently.
- Everywhere else, only `.flac` files are scanned and shown — **except**
  **Check AcoustID (incl. MP3)**, which rescans the folder for both
  `.flac` and `.mp3` files and runs the check on all of them. This is
  scoped strictly to that one button: it doesn't touch what Start or
  Check AcoustID Only see, doesn't convert anything, and, like the
  FLAC-only check, never auto-corrects.
- The API key is saved between runs (Qt `QSettings`); get one for free at
  [acoustid.org](https://acoustid.org/).

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
- A log file `acoustid-convert-<timestamp>.log` (or
  `acoustid-check-<timestamp>.log` for an AcoustID-only run) is written in
  the scanned root folder, containing ffmpeg output for any failures.
- Re-running on the same folder is safe: files that already converted no
  longer have a `.flac` source and are skipped automatically, so an
  interrupted run on a large library can simply be restarted.

## Code layout

- `core.py` — framework-agnostic conversion logic (scanning, ffmpeg
  invocation, tag/picture copying); no Qt dependency, directly unit tested.
- `acoustid.py` — the PySide6 window and its background conversion thread.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
