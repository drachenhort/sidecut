// Package core is the Go port of core.py's conversion pipeline: finding
// FLAC files, shelling out to ffmpeg, and copying tags onto the result.
//
// Not yet ported: apply_release_type/apply_release_provenance/
// correct_acoustid_mismatch, which rewrite a FLAC's own Vorbis comments in
// place - internal/flactag is read-only today, and those three need a
// FLAC metadata *writer*, which is its own scoped piece of work (see the
// plan doc's open questions).
package core

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"sidecut-go/internal/flactag"
)

var QualityPresets = map[string][]string{
	"v0":     {"-q:a", "0"},
	"v2":     {"-q:a", "2"},
	"cbr320": {"-b:a", "320k"},
}

var QualityLabels = map[string]string{
	"v0":     "V0 VBR (~245kbps)",
	"v2":     "V2 VBR (~190kbps)",
	"cbr320": "320kbps CBR",
}

// CheckFFmpeg reports whether ffmpeg is on PATH.
func CheckFFmpeg() bool {
	_, err := exec.LookPath("ffmpeg")
	return err == nil
}

// FindFLACFiles returns every .flac file under root, sorted.
func FindFLACFiles(root string) ([]string, error) {
	return findFiles(root, ".flac")
}

// FindFLACAndMP3Files returns every .flac/.mp3 file under root, sorted.
func FindFLACAndMP3Files(root string) ([]string, error) {
	return findFiles(root, ".flac", ".mp3")
}

func findFiles(root string, exts ...string) ([]string, error) {
	var out []string
	err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			return nil
		}
		lower := strings.ToLower(filepath.Ext(path))
		for _, ext := range exts {
			if lower == ext {
				out = append(out, path)
				return nil
			}
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	sortStrings(out)
	return out, nil
}

func sortStrings(s []string) {
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && s[j-1] > s[j]; j-- {
			s[j-1], s[j] = s[j], s[j-1]
		}
	}
}

// ConversionResult mirrors core.py's ConversionResult dataclass.
type ConversionResult struct {
	Source   string
	OK       bool
	Message  string
	SrcBytes int64
	DstBytes int64
}

// Progress mirrors core.py's Progress dataclass - one update of a
// running conversion's state, passed to a callback.
type Progress struct {
	OutTimeSeconds float64
	Speed          string
	Percent        float64
}

func applyProgressLine(p *Progress, line string, totalDuration time.Duration) {
	key, value, ok := strings.Cut(line, "=")
	if !ok {
		return
	}
	switch key {
	case "out_time_ms":
		if us, err := strconv.ParseInt(strings.TrimSpace(value), 10, 64); err == nil {
			p.OutTimeSeconds = float64(us) / 1_000_000
		}
	case "speed":
		p.Speed = strings.TrimSpace(value)
	}
	if totalDuration > 0 {
		p.Percent = min(100.0, p.OutTimeSeconds/totalDuration.Seconds()*100)
	}
}

const CancelKillTimeout = 5 * time.Second

// RunFFmpeg shells out to ffmpeg to convert src to dst, streaming
// -progress pipe:1 updates to onProgress (may be nil) and writing
// stderr to log. shouldCancel (may be nil) is polled between progress
// lines; on cancel, ffmpeg is asked to terminate. Returns whether ffmpeg
// exited 0.
func RunFFmpeg(
	src, dst string,
	qualityArgs []string,
	log io.Writer,
	totalDuration time.Duration,
	onProgress func(Progress),
	shouldCancel func() bool,
) (bool, error) {
	if shouldCancel != nil && shouldCancel() {
		return false, nil
	}

	args := []string{"-y", "-nostdin", "-loglevel", "error", "-nostats",
		"-i", src, "-map", "0:a", "-map_metadata", "-1"}
	args = append(args, qualityArgs...)
	args = append(args, "-progress", "pipe:1", dst)

	cmd := exec.Command("ffmpeg", args...)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return false, err
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return false, err
	}
	if err := cmd.Start(); err != nil {
		return false, err
	}

	var stderrBuf strings.Builder
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		io.Copy(&stderrBuf, stderr)
	}()

	progress := Progress{}
	scanner := bufio.NewScanner(stdout)
	for scanner.Scan() {
		if shouldCancel != nil && shouldCancel() {
			cmd.Process.Kill()
			break
		}
		applyProgressLine(&progress, strings.TrimSpace(scanner.Text()), totalDuration)
		if onProgress != nil {
			onProgress(progress)
		}
	}

	waitErr := cmd.Wait()
	wg.Wait()
	if stderrBuf.Len() > 0 {
		fmt.Fprint(log, stderrBuf.String())
	}
	return waitErr == nil, nil
}

// ConvertOne converts one FLAC file to MP3, copies its tags, and removes
// the source on success. Mirrors core.py's convert_one: any failure is
// reported in the returned ConversionResult rather than as an error, so
// a caller processing a batch can account for every file.
func ConvertOne(
	src string,
	qualityArgs []string,
	log io.Writer,
	onProgress func(Progress),
	shouldCancel func() bool,
) ConversionResult {
	dst := strings.TrimSuffix(src, filepath.Ext(src)) + ".mp3"

	info, err := os.Stat(src)
	if err != nil {
		os.Remove(dst)
		fmt.Fprintf(log, "FAILED: %s: %v\n", src, err)
		return ConversionResult{Source: src, OK: false, Message: "conversion failed, see log"}
	}
	srcBytes := info.Size()
	duration := TrackDuration(src)

	ok, err := RunFFmpeg(src, dst, qualityArgs, log, duration, onProgress, shouldCancel)
	if err != nil {
		os.Remove(dst)
		fmt.Fprintf(log, "FAILED: %s: %v\n", src, err)
		return ConversionResult{Source: src, OK: false, Message: "conversion failed, see log"}
	}
	if ok {
		if dstInfo, statErr := os.Stat(dst); statErr != nil || dstInfo.Size() == 0 {
			ok = false
		}
	}

	if ok {
		if err := CopyTags(src, dst); err != nil {
			ok = false
			fmt.Fprintf(log, "tag copy failed for %s: %v\n", src, err)
		}
	}

	if ok {
		dstInfo, _ := os.Stat(dst)
		var dstBytes int64
		if dstInfo != nil {
			dstBytes = dstInfo.Size()
		}
		if err := os.Remove(src); err != nil {
			fmt.Fprintf(log, "warning: converted %s but could not delete source: %v\n", src, err)
		}
		return ConversionResult{Source: src, OK: true, SrcBytes: srcBytes, DstBytes: dstBytes}
	}

	os.Remove(dst)
	if shouldCancel != nil && shouldCancel() {
		return ConversionResult{Source: src, OK: false, Message: "cancelled"}
	}
	fmt.Fprintf(log, "FAILED: %s\n", src)
	return ConversionResult{Source: src, OK: false, Message: "conversion failed, see log"}
}

// TrackDuration reads a FLAC's stream duration; 0 if it can't be read
// (duration is cosmetic, never fatal - mirrors core.py's track_duration
// swallowing all errors).
func TrackDuration(path string) time.Duration {
	tags, err := flactag.Read(path)
	if err != nil {
		return 0
	}
	return tags.Duration
}
