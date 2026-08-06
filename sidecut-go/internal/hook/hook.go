// Package hook is the Go port of lidarr_hook.py: the headless entry point
// for running sidecut as a Lidarr Custom Script. Lidarr calls it directly
// after grabbing/importing a release, passing details as lidarr_*
// environment variables.
//
// Not yet ported: handing converted files off to Lidarr's Manual Import
// API (lidarr.py's import_folder isn't ported yet either - see
// internal/lidarr's package doc). RunFromEnvironment still converts every
// added FLAC; it just can't queue the reimport step yet, and says so
// rather than silently skipping it.
package hook

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"sidecut-go/internal/config"
	"sidecut-go/internal/core"
)

// supportedEvents are the only lidarr_eventtype values this hook does
// anything with. Everything else (Grab, Rename, TrackRetag, ArtistAdd,
// ArtistDelete, AlbumDelete, ApplicationUpdate, HealthIssue,
// ManualInteractionRequired) is ignored.
var supportedEvents = map[string]bool{"Download": true, "Test": true}

// IsInvocation reports whether this process was launched by Lidarr as a
// Custom Script.
func IsInvocation() bool {
	_, ok := os.LookupEnv("lidarr_eventtype")
	return ok
}

func env(name string) string {
	return os.Getenv("lidarr_" + name)
}

func openLog(logPath string) (*os.File, error) {
	f, err := os.OpenFile(logPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err == nil {
		return f, nil
	}
	home, homeErr := os.UserHomeDir()
	if homeErr != nil {
		return nil, err
	}
	fallbackDir := filepath.Join(home, ".local", "share", "AcoustID", "logs")
	if mkErr := os.MkdirAll(fallbackDir, 0o755); mkErr != nil {
		return nil, err
	}
	return os.OpenFile(filepath.Join(fallbackDir, filepath.Base(logPath)), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
}

// RunFromEnvironment handles one Lidarr Custom Script invocation using the
// current process's environment variables. Returns a process exit code (0
// on success, non-zero if conversion hit a problem). quality is the
// conversion preset to use; there's no QSettings-equivalent GUI setting
// to read it from in this headless-only port, so callers pass a fixed
// default ("v0") unless/until this gains its own config.ini field.
func RunFromEnvironment(quality string) int {
	eventtype := env("eventtype")

	if eventtype == "Test" {
		fmt.Println("Lidarr test event received; nothing to do.")
		return 0
	}
	if !supportedEvents[eventtype] {
		fmt.Printf("lidarr_eventtype=%q is not handled by this hook; ignoring.\n", eventtype)
		return 0
	}

	pathsRaw := env("addedtrackpaths")
	if pathsRaw == "" {
		pathsRaw = env("trackfile_path")
	}
	if pathsRaw == "" {
		fmt.Fprintln(os.Stderr, "No lidarr_addedtrackpaths/lidarr_trackfile_path in the environment; nothing to convert.")
		return 1
	}

	var flacPaths []string
	for _, p := range strings.Split(pathsRaw, "|") {
		if p != "" && strings.EqualFold(filepath.Ext(p), ".flac") {
			flacPaths = append(flacPaths, p)
		}
	}
	if len(flacPaths) == 0 {
		fmt.Println("None of the added tracks are FLAC; nothing to convert.")
		return 0
	}

	if _, ok := core.QualityPresets[quality]; !ok {
		quality = "v0"
	}

	artistPath := env("artist_path")
	var folder string
	if artistPath != "" {
		folder = artistPath
	} else {
		folder = filepath.Dir(flacPaths[0])
	}
	logPath := filepath.Join(folder, fmt.Sprintf("acoustid-lidarr-hook-%s.log", time.Now().Format("20060102-150405")))

	log, err := openLog(logPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Could not open log file: %v\n", err)
		return 1
	}
	var logWriter io.Writer = log

	okCount := 0
	for _, path := range flacPaths {
		result := core.ConvertOne(path, core.QualityPresets[quality], logWriter, nil, nil)
		if result.OK {
			okCount++
		} else {
			fmt.Fprintf(os.Stderr, "Conversion failed for %s: %s\n", path, result.Message)
		}
	}
	log.Close()
	fmt.Printf("Converted %d/%d FLAC file(s).\n", okCount, len(flacPaths))

	resolved := config.LoadSettings("")
	lidarrURL := resolved["lidarr_url"]
	apiKey := resolved["lidarr_api_key"]
	if lidarrURL == "" || apiKey == "" {
		fmt.Printf(
			"No Lidarr URL/API key configured (run sidecut --configure, or edit %s); skipping "+
				"the re-import step. Lidarr's own disk rescan will eventually pick up the new MP3s.\n",
			config.ResolveConfigPath(),
		)
		return exitCode(okCount, len(flacPaths))
	}

	// lidarr.ImportFolder isn't ported yet (see internal/lidarr's package
	// doc) - say so rather than silently skipping without explanation.
	fmt.Println("Lidarr URL/API key are configured, but the Go port doesn't queue the Lidarr " +
		"reimport step yet. Lidarr's own disk rescan will eventually pick up the new MP3s.")
	return exitCode(okCount, len(flacPaths))
}

func exitCode(okCount, total int) int {
	if okCount == total {
		return 0
	}
	return 1
}
