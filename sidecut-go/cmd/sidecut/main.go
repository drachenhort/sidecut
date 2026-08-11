// Command sidecut is the Go port's headless CLI entrypoint: dispatches to
// the Lidarr Custom Script hook mode, the --configure text UI, or a plain
// `sidecut convert <folder>` batch conversion - the headless equivalent
// of the Python GUI's Transcode button. No GUI in this port; see the
// plan doc (docs/plans/go-port-headless.md) for what that would take.
package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"sidecut-go/internal/acoustid"
	"sidecut-go/internal/config"
	"sidecut-go/internal/configureui"
	"sidecut-go/internal/core"
	"sidecut-go/internal/hook"
	"sidecut-go/internal/lidarr"
)

func main() {
	if path := config.LoadSettings("")["ffmpeg_path"]; path != "" {
		core.FFmpegPath = path
	}

	if hook.IsInvocation() {
		// Lidarr Custom Script invocation: no terminal to interact with,
		// so never prompt - just the plain conversion logic.
		os.Exit(hook.RunFromEnvironment("v0"))
	}

	if len(os.Args) > 1 && os.Args[1] == "--configure" {
		os.Exit(configureui.Run(lidarr.CheckConnection))
	}

	if len(os.Args) > 1 && os.Args[1] == "convert" {
		if len(os.Args) < 3 {
			fmt.Fprintln(os.Stderr, "usage: sidecut convert <folder> [quality: v0|v2|cbr320]")
			os.Exit(2)
		}
		os.Exit(runConvert(os.Args[2], qualityArg()))
	}

	if len(os.Args) > 1 && os.Args[1] == "check" {
		if len(os.Args) < 3 {
			fmt.Fprintln(os.Stderr, "usage: sidecut check <file-or-folder>")
			os.Exit(2)
		}
		os.Exit(runCheck(os.Args[2]))
	}

	printUsage()
	os.Exit(2)
}

func qualityArg() string {
	if len(os.Args) > 3 {
		if _, ok := core.QualityPresets[os.Args[3]]; ok {
			return os.Args[3]
		}
	}
	return "v0"
}

func printUsage() {
	fmt.Fprintln(os.Stderr, "usage:")
	fmt.Fprintln(os.Stderr, "  sidecut convert <folder> [v0|v2|cbr320]   convert every FLAC under folder to MP3")
	fmt.Fprintln(os.Stderr, "  sidecut check <file-or-folder>            check FLAC/MP3 file(s) against AcoustID")
	fmt.Fprintln(os.Stderr, "  sidecut --configure                       set AcoustID/Lidarr API keys interactively")
	fmt.Fprintln(os.Stderr, "  (no GUI in this Go port - see the flac2mp3 Python project for that)")
}

// runConvert is the headless equivalent of the GUI's Transcode button:
// finds every FLAC under folder and converts each one in turn, printing a
// one-line result per file and a summary at the end.
func runConvert(folder, quality string) int {
	if !core.CheckFFmpeg() {
		fmt.Fprintf(os.Stderr, "ffmpeg is required but was not found (looked for %q; set ffmpeg_path via --configure or $FFMPEG_PATH to point at it).\n", core.FFmpegPath)
		return 1
	}

	files, err := core.FindFLACFiles(folder)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Could not scan %s: %v\n", folder, err)
		return 1
	}
	if len(files) == 0 {
		fmt.Println("No FLAC files found.")
		return 0
	}
	fmt.Printf("Found %d FLAC file(s). Converting at quality %q...\n", len(files), quality)

	checker := acoustidCheckerFromConfig()

	var logWriter io.Writer = os.Stderr
	okCount := 0
	for i, path := range files {
		fmt.Printf("[%d/%d] %s ... ", i+1, len(files), path)
		result := core.ConvertOne(path, core.QualityPresets[quality], logWriter, nil, nil)
		if !result.OK {
			fmt.Println("FAILED: " + result.Message)
			continue
		}
		okCount++
		fmt.Println("ok")
		if checker != nil {
			dst := mp3PathFor(path)
			check := checker.Check(dst)
			fmt.Printf("      AcoustID: %s - %s\n", check.Status, check.Detail)
		}
	}

	fmt.Printf("Converted %d/%d file(s).\n", okCount, len(files))
	if okCount != len(files) {
		return 1
	}
	return 0
}

// mp3PathFor mirrors core.ConvertOne's own dst derivation: same directory
// and basename as src, .mp3 extension.
func mp3PathFor(src string) string {
	return strings.TrimSuffix(src, filepath.Ext(src)) + ".mp3"
}

// acoustidCheckerFromConfig returns a Checker if an AcoustID API key is
// configured and fpcalc is on PATH, else nil - AcoustID checking is
// opt-in by configuration, not a hard requirement to convert. Prints a
// one-time warning if a key is configured but fpcalc is missing, so a
// silently-skipped check doesn't look like a false "no mismatch" result.
func acoustidCheckerFromConfig() *acoustid.Checker {
	apiKey := config.LoadSettings("")["acoustid_api_key"]
	if apiKey == "" {
		return nil
	}
	if !acoustid.CheckFpcalc() {
		fmt.Fprintln(os.Stderr, "AcoustID API key is configured but fpcalc was not found on PATH; skipping AcoustID checks.")
		return nil
	}
	return acoustid.NewChecker(apiKey)
}

// runCheck is the headless equivalent of the GUI's "Check AcoustID"
// button: checks a single file, or every FLAC/MP3 file under a folder,
// against AcoustID/MusicBrainz and prints one line per file. Purely
// informational - never modifies a file (see internal/acoustid's package
// doc for why autocorrect isn't ported yet).
func runCheck(path string) int {
	apiKey := config.LoadSettings("")["acoustid_api_key"]
	if apiKey == "" {
		fmt.Fprintln(os.Stderr, "No AcoustID API key configured. Run `sidecut --configure` first.")
		return 1
	}
	if !acoustid.CheckFpcalc() {
		fmt.Fprintln(os.Stderr, "fpcalc is required but was not found on PATH.")
		return 1
	}

	files, err := filesToCheck(path)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Could not resolve %s: %v\n", path, err)
		return 1
	}
	if len(files) == 0 {
		fmt.Println("No FLAC/MP3 files found.")
		return 0
	}

	checker := acoustid.NewChecker(apiKey)
	problems := 0
	for i, f := range files {
		result := checker.Check(f)
		fmt.Printf("[%d/%d] %s: %s - %s\n", i+1, len(files), f, result.Status, result.Detail)
		if result.Status == "mismatch" || result.Status == "error" {
			problems++
		}
	}
	if problems > 0 {
		return 1
	}
	return 0
}

// filesToCheck resolves path to the list of files runCheck should check:
// itself if it's a file, every FLAC/MP3 under it if it's a folder.
func filesToCheck(path string) ([]string, error) {
	info, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	if !info.IsDir() {
		return []string{path}, nil
	}
	return core.FindFLACAndMP3Files(path)
}
