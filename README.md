# flac2mp3

Recursively transcode a folder of FLAC files to MP3, in place, with a simple
text-based interactive UI.

## Requirements

- `ffmpeg` on `PATH`
- Python 3.10+
- `pip install -r requirements.txt` (just `mutagen`, used for tag copying)

## Usage

```bash
python3 flac2mp3.py                       # prompts for folder + quality
python3 flac2mp3.py /path/to/music        # prompts for quality only
python3 flac2mp3.py /path/to/music --quality v0 --yes   # fully non-interactive
```

Options:

- `--quality {v0,v2,cbr320}` — MP3 quality preset (V0 VBR, V2 VBR, or 320kbps CBR)
- `--yes` — skip the confirmation prompt
- `--workers N` — number of parallel ffmpeg jobs (default: up to 4, based on CPU count)
- `--plain` — disable the live dashboard and use one `\r`-updated progress
  line instead (useful when piping output to a file/log)

## Live progress window

When run in a real terminal, `flac2mp3` shows a live dashboard (similar in
spirit to `radiotop`'s live view): one line per active `ffmpeg` worker with
its current file, percent complete, elapsed time, and encode speed, plus a
scrolling list of recently finished files. It updates a few times a second
by reading `ffmpeg`'s machine-readable `-progress` stream.

When stdout isn't a TTY (piped, redirected to a file, or run with
`--plain`), it automatically falls back to a single progress line instead.

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
- A log file `flac2mp3-<timestamp>.log` is written in the scanned root
  folder, containing ffmpeg output for any failures.
- Re-running the script on the same folder is safe: files that already
  converted no longer have a `.flac` source and are skipped automatically,
  so an interrupted run on a large library can simply be restarted.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
