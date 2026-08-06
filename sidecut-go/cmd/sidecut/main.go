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

	"sidecut-go/internal/configureui"
	"sidecut-go/internal/core"
	"sidecut-go/internal/hook"
	"sidecut-go/internal/lidarr"
)

func main() {
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
	fmt.Fprintln(os.Stderr, "  sidecut --configure                       set AcoustID/Lidarr API keys interactively")
	fmt.Fprintln(os.Stderr, "  (no GUI in this Go port - see the flac2mp3 Python project for that)")
}

// runConvert is the headless equivalent of the GUI's Transcode button:
// finds every FLAC under folder and converts each one in turn, printing a
// one-line result per file and a summary at the end.
func runConvert(folder, quality string) int {
	if !core.CheckFFmpeg() {
		fmt.Fprintln(os.Stderr, "ffmpeg is required but was not found on PATH.")
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

	var logWriter io.Writer = os.Stderr
	okCount := 0
	for i, path := range files {
		fmt.Printf("[%d/%d] %s ... ", i+1, len(files), path)
		result := core.ConvertOne(path, core.QualityPresets[quality], logWriter, nil, nil)
		if result.OK {
			okCount++
			fmt.Println("ok")
		} else {
			fmt.Println("FAILED: " + result.Message)
		}
	}

	fmt.Printf("Converted %d/%d file(s).\n", okCount, len(files))
	if okCount != len(files) {
		return 1
	}
	return 0
}
