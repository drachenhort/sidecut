// Package config is the Go port of config.py: a plain-text config.ini for
// headless/SSH setups (e.g. Unraid), read/written by --configure and used
// as a fallback wherever the GUI's QSettings would otherwise be the only
// way to set the AcoustID/Lidarr API keys.
//
// There is no QSettings equivalent here (that was GUI-only, out of scope
// for this headless-only port) - precedence is just env vars > config.ini,
// highest wins.
package config

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// Fields are the config keys this package knows about, in the order
// they're written to the file.
var Fields = []string{"acoustid_api_key", "lidarr_url", "lidarr_api_key"}

var FieldLabels = map[string]string{
	"acoustid_api_key": "AcoustID API key",
	"lidarr_url":       "Lidarr URL",
	"lidarr_api_key":   "Lidarr API key",
}

var EnvVars = map[string]string{
	"acoustid_api_key": "ACOUSTID_API_KEY",
	"lidarr_url":       "LIDARR_URL",
	"lidarr_api_key":   "LIDARR_API_KEY",
}

const section = "flac2mp3"

// ConfigPath returns ~/.config/flac2mp3/config.ini, honoring
// $XDG_CONFIG_HOME the same way config.py's CONFIG_PATH does.
func ConfigPath() string {
	base := os.Getenv("XDG_CONFIG_HOME")
	if base == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			home = "."
		}
		base = filepath.Join(home, ".config")
	}
	return filepath.Join(base, "flac2mp3", "config.ini")
}

// ScriptDir is the directory the running binary was launched from,
// however it was invoked - the Go equivalent of
// Path(sys.argv[0]).resolve().parent.
func ScriptDir() string {
	exe := os.Args[0]
	abs, err := filepath.Abs(exe)
	if err != nil {
		return "."
	}
	resolved, err := filepath.EvalSymlinks(abs)
	if err != nil {
		resolved = abs
	}
	return filepath.Dir(resolved)
}

func isFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

// ResolveConfigPath mirrors config.py's resolve_config_path(): config.ini
// next to the binary wins if it exists; otherwise ConfigPath() if that
// file exists; otherwise - a true first run with neither present - default
// to creating it next to the binary if that directory is writable, else
// ConfigPath().
func ResolveConfigPath() string {
	local := filepath.Join(ScriptDir(), "config.ini")
	if isFile(local) {
		return local
	}
	cfgPath := ConfigPath()
	if isFile(cfgPath) {
		return cfgPath
	}
	if isWritableDir(ScriptDir()) {
		return local
	}
	return cfgPath
}

// isWritableDir probes writability by attempting to create and remove a
// throwaway file, rather than checking permission bits directly - simpler
// than syscall-level access checks and correct across filesystems/ACLs
// that permission bits alone don't capture.
func isWritableDir(dir string) bool {
	info, err := os.Stat(dir)
	if err != nil || !info.IsDir() {
		return false
	}
	probe, err := os.CreateTemp(dir, ".write-test-*")
	if err != nil {
		return false
	}
	name := probe.Name()
	probe.Close()
	os.Remove(name)
	return true
}

// ReadFile reads just the config file (no env merge) - what --configure
// shows/edits as "what's actually on disk". Any read/parse error (missing
// file, malformed ini) is swallowed and returns an empty map, matching
// config.py's forgiving behavior - a bad/missing config file should never
// crash the program.
func ReadFile(path string) map[string]string {
	f, err := os.Open(path)
	if err != nil {
		return map[string]string{}
	}
	defer f.Close()

	values := map[string]string{}
	inSection := false
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, ";") || strings.HasPrefix(line, "#") {
			continue
		}
		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			inSection = strings.TrimSpace(line[1:len(line)-1]) == section
			continue
		}
		if !inSection {
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		key = strings.ToLower(strings.TrimSpace(key))
		value = strings.TrimSpace(value)
		if value != "" {
			values[key] = value
		}
	}
	if err := scanner.Err(); err != nil {
		return map[string]string{}
	}

	result := map[string]string{}
	for _, field := range Fields {
		if v, ok := values[field]; ok {
			result[field] = v
		}
	}
	return result
}

// LoadSettings merges the config file (at configPath, or the resolved
// default if configPath is "") with env var overrides, env vars winning.
func LoadSettings(configPath string) map[string]string {
	if configPath == "" {
		configPath = ResolveConfigPath()
	}
	result := ReadFile(configPath)

	for field, envVar := range EnvVars {
		if v := os.Getenv(envVar); v != "" {
			result[field] = v
		}
	}
	return result
}

// SaveFile writes values over whatever's already in the file at path (or
// the resolved default if path is ""); fields not in values are left
// untouched. Creates the parent directory if needed.
func SaveFile(values map[string]string, path string) error {
	if path == "" {
		path = ResolveConfigPath()
	}
	merged := ReadFile(path)
	for k, v := range values {
		merged[k] = v
	}

	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}

	var b strings.Builder
	b.WriteString("[" + section + "]\n")
	for _, field := range Fields {
		fmt.Fprintf(&b, "%s = %s\n", field, merged[field])
	}
	return os.WriteFile(path, []byte(b.String()), 0o644)
}
