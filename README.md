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

## Lidarr import (optional)

- Entirely separate feature, off by default. Click **Lidarr Settings...**
  to enter your Lidarr **URL** (e.g. `http://localhost:8686`) and **API
  key** (Lidarr's Settings > General) and use **Test Connection** to
  confirm they're correct (host/port reachable, key accepted) before
  relying on them. Then click **Import to Lidarr** to hand the current
  folder to Lidarr, instead of using Lidarr's own import UI.
- Tick **Auto-import to Lidarr after conversion** to skip that manual
  click: whenever **Start** finishes converting at least one file, the
  same import runs automatically right after. Off by default. It only
  runs after a real conversion (never after Check AcoustID Only/+MP3,
  which are meant to leave files untouched), and only if a URL/API key
  are already set - if they're not, it's silently skipped rather than
  popping up an error, since ticking the checkbox without finishing setup
  isn't a mistake worth interrupting you over.
- If this app sees the library at a different path than Lidarr does (e.g.
  Lidarr runs in a container or on another host, and the same share is
  mounted under a different path here), also set **Local path to
  library** and **Same path inside Lidarr** in Lidarr Settings... - e.g.
  `/home/user/Music` and `/music`. Without this, Lidarr's manual-import
  scan will silently find nothing, since it can only look things up under
  its own filesystem view. Check an artist's `path` in Lidarr (via its
  UI, or `GET /api/v1/artist`) if you're not sure what Lidarr's side
  should be. Leave both blank if this app and Lidarr already agree on
  paths.
- This does **not** write to Lidarr's database directly - that's an
  unsupported, version-fragile approach that can race with Lidarr's own
  in-memory state. Instead it drives Lidarr's **Manual Import API**: the
  same matching logic behind Lidarr's Manual Import screen, just called
  as an HTTP command instead of clicked through by hand.
- If Lidarr rejects a match because its database still has a TrackFile
  record for a file this tool already replaced (e.g. converted a FLAC to
  MP3 and deleted the original), that stale record is deleted via
  Lidarr's API and the scan is retried once before giving up - the same
  fix used by the proven
  [TheCaptain989/lidarr-flac2mp3](https://github.com/TheCaptain989/lidarr-flac2mp3)
  script, rather than a blanket library rescan. **Note**: `DELETE
  /api/v1/trackfile` removes the actual file, not just the database row,
  so this only ever deletes a record after confirming (via a real
  filesystem check) that its file is genuinely gone - never every record
  for the album. This is exactly why getting the path mapping right
  (above) matters: without it, this check can't tell a missing file from
  one it simply can't see.
- Lidarr reads the files' own embedded tags to propose matches. Since
  this tool preserves full MusicBrainz/AcoustID tags through conversion,
  well-tagged files are usually auto-matched with no input needed.
- Only files Lidarr fully auto-matches (artist, album, and track, with no
  unresolved rejections) are submitted for import; anything it can't
  match is left completely alone, and the result lists each skipped file
  together with Lidarr's own reason (e.g. "already has file") so a
  surprising "0 imported" is easy to diagnose instead of a dead end.
- Runs in the background and is independent of everything else in the
  window - it doesn't require or interact with Start, Check AcoustID, or
  the file table, and can be used on its own at any time a folder is
  selected (e.g. right after a conversion finishes).

## Automatic conversion on new downloads (optional)

Register this program as a Lidarr **Custom Script** to have it convert and
import new downloads automatically, with no GUI involved:

1. Configure the Lidarr URL/API key once via **Lidarr Settings...** in the
   GUI (see above) - the headless hook reuses the same saved settings.
2. In Lidarr: **Settings → Connect → +  → Custom Script**.
3. **Path**: the full path to `acoustid.py` (it's already executable and
   has a `#!/usr/bin/env python3` shebang).
4. Tick **On Import** (and **On Upgrade** if you also want re-conversions
   on upgrades), then **Save** and use Lidarr's **Test** button to verify.

From then on, whenever Lidarr imports a new FLAC download, it invokes
`acoustid.py` directly with details as `lidarr_*` environment variables
(the same mechanism `lidarr-flac2mp3` uses) instead of opening a window:
it converts the newly added FLAC(s) using your saved quality preset, then
- if a Lidarr URL/API key are configured - hands the result to Lidarr's
Manual Import API, same as the **Import to Lidarr** button. Any other
Lidarr event (Grab, Rename, etc.) is ignored. To try it by hand without
waiting for a real download:

```bash
lidarr_eventtype=Test /full/path/to/acoustid.py
```

See `lidarr_hook.py` for the implementation.

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
- `lidarr.py` — the optional Lidarr Manual Import API client; no Qt
  dependency, directly unit tested.
- `lidarr_hook.py` — headless entry point for running as a Lidarr Custom
  Script (reads `lidarr_*` env vars, converts, calls `lidarr.py`); no Qt
  widgets, directly unit tested.
- `acoustid.py` — the PySide6 window and its background conversion
  thread; `main()` dispatches to `lidarr_hook` before touching Qt if
  invoked as a Lidarr Custom Script.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
