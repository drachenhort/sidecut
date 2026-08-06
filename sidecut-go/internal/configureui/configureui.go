// Package configureui is the Go port of configure_cli.py: the
// `--configure` interactive text UI that lets a headless/SSH user set the
// AcoustID/Lidarr API keys without a GUI.
package configureui

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"strings"

	"sidecut-go/internal/config"
)

const maskVisible = 4

func mask(value string) string {
	if len(value) <= maskVisible {
		return strings.Repeat("*", len(value))
	}
	return value[:maskVisible] + strings.Repeat("*", len(value)-maskVisible)
}

// LidarrChecker mirrors lidarr.check_connection: returns the Lidarr
// version string on success. A nil LidarrChecker passed to Run means the
// check can't be attempted at all (the Python original's equivalent of
// `import lidarr` failing because `requests` isn't installed) - Run
// treats that the same as a skipped check, not a failure.
type LidarrChecker func(url, apiKey string) (version string, err error)

// ioPrompter bundles the reader/writer Run operates on so tests can
// substitute both without touching real stdin/stdout.
type ioPrompter struct {
	in  *bufio.Reader
	out io.Writer
}

func (p *ioPrompter) printf(format string, args ...any) {
	fmt.Fprintf(p.out, format, args...)
}

func (p *ioPrompter) println(args ...any) {
	fmt.Fprintln(p.out, args...)
}

func (p *ioPrompter) prompt(text string) string {
	p.printf("%s", text)
	line, _ := p.in.ReadString('\n')
	return strings.TrimSpace(line)
}

// Run executes the interactive configuration flow against stdin/stdout,
// returning a process exit code (0 saved-or-nothing-to-do, 1 declined).
func Run(checkLidarr LidarrChecker) int {
	return run(&ioPrompter{in: bufio.NewReader(os.Stdin), out: os.Stdout}, checkLidarr)
}

func run(p *ioPrompter, checkLidarr LidarrChecker) int {
	path := config.ResolveConfigPath()

	p.println(strings.Repeat("=", 44))
	p.println(" Sidecut - Headless Configuration")
	p.printf(" File: %s\n", path)
	p.println(strings.Repeat("=", 44))
	p.println()
	p.println("Leave blank to keep current value. Values in [brackets] show")
	p.println("what's currently set (env vars, if any, always win over this")
	p.println("file and are shown for reference, not editable here).")
	p.println()

	current := config.ReadFile(path)
	updates := map[string]string{}
	for _, field := range config.Fields {
		envValue := os.Getenv(config.EnvVars[field])
		newValue, changed := promptField(p, field, current[field], envValue)
		if changed {
			updates[field] = newValue
		}
		p.println()
	}

	if len(updates) == 0 {
		p.println("Nothing changed.")
		return 0
	}

	p.println(strings.Repeat("-", 44))
	p.println(" Review")
	p.println(strings.Repeat("-", 44))
	merged := map[string]string{}
	for k, v := range current {
		merged[k] = v
	}
	for k, v := range updates {
		merged[k] = v
	}
	for _, field := range config.Fields {
		value := merged[field]
		if value == "" {
			continue
		}
		tag := ""
		if _, ok := updates[field]; !ok {
			tag = " (unchanged)"
		}
		p.printf("  %-17s: %s%s\n", field, mask(value), tag)
	}
	p.println()

	verified := verify(p, merged, checkLidarr)
	p.println()

	var promptText string
	defaultYes := verified
	if verified {
		promptText = fmt.Sprintf("Save to %s ? [Y/n] > ", path)
	} else {
		promptText = fmt.Sprintf("Lidarr check failed - save to %s anyway? [y/N] > ", path)
	}

	answer := strings.ToLower(p.prompt(promptText))
	proceed := answer == "y" || answer == "yes" || (answer == "" && defaultYes)
	if !proceed {
		p.println("Not saved.")
		return 1
	}

	if err := config.SaveFile(updates, path); err != nil {
		p.printf("Error saving: %v\n", err)
		return 1
	}
	p.println()
	p.println("Saved. the hook mode and the Settings dialog will pick this up.")
	p.println(strings.Repeat("=", 44))
	return 0
}

// promptField returns the new value to save and whether it changed at
// all (false means "leave unchanged" - either left blank, or overridden
// by an env var so editing here would be moot).
func promptField(p *ioPrompter, field, current, envValue string) (string, bool) {
	label := config.FieldLabels[field]
	if envValue != "" {
		p.printf("%-18s [env %s=%s - overrides file, editing here has no effect]\n", label, config.EnvVars[field], envValue)
		p.prompt("> ")
		return "", false
	}

	shown := "not set"
	if current != "" {
		shown = mask(current)
	}
	p.printf("%-18s [%s]\n", label, shown)
	typed := p.prompt("> ")
	if typed == "" {
		return "", false
	}
	return typed, true
}

// effective is what will actually be used at runtime for field: env var
// wins over whatever's in the file.
func effective(field string, merged map[string]string) string {
	if v := os.Getenv(config.EnvVars[field]); v != "" {
		return v
	}
	return merged[field]
}

// verify runs whatever checks are possible against the values that will
// actually be effective (env vars included). Returns false only when a
// check ran and definitively failed - never for skipped/unattempted
// checks, since those aren't a reason to block saving.
func verify(p *ioPrompter, merged map[string]string, checkLidarr LidarrChecker) bool {
	url := effective("lidarr_url", merged)
	apiKey := effective("lidarr_api_key", merged)
	if url == "" || apiKey == "" {
		return true
	}

	p.println("Verifying Lidarr connection...")
	if checkLidarr == nil {
		p.println("  Lidarr: skipped - no connection checker available")
		return true
	}

	version, err := checkLidarr(url, apiKey)
	if err != nil {
		p.printf("  Lidarr: FAILED - %s\n", err)
		return false
	}
	p.printf("  Lidarr: OK - connected - Lidarr v%s\n", version)
	return true
}
