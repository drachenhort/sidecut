package configureui

import (
	"bufio"
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"sidecut-go/internal/config"
)

// withIsolatedConfig points config.ScriptDir()/config.ConfigPath() at a
// scratch temp dir for the duration of the test, so Run()'s call to
// config.ResolveConfigPath() never touches the real binary's directory or
// the real ~/.config/flac2mp3/config.ini.
func withIsolatedConfig(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	origArgv0 := os.Args[0]
	os.Args[0] = filepath.Join(dir, "sidecut")
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(t.TempDir(), "empty-home-config"))
	t.Cleanup(func() { os.Args[0] = origArgv0 })
	return dir
}

func runWithInput(t *testing.T, input string, checkLidarr LidarrChecker) (int, string) {
	t.Helper()
	var out bytes.Buffer
	p := &ioPrompter{in: bufio.NewReader(strings.NewReader(input)), out: &out}
	code := run(p, checkLidarr)
	return code, out.String()
}

func TestMaskShortValueFullyHidden(t *testing.T) {
	if got := mask("ab"); got != "**" {
		t.Errorf("mask(ab) = %q, want **", got)
	}
}

func TestMaskLongValueKeepsPrefix(t *testing.T) {
	if got := mask("abcd1234"); got != "abcd****" {
		t.Errorf("mask(abcd1234) = %q, want abcd****", got)
	}
}

func TestFreshConfigPromptsAndSaves(t *testing.T) {
	dir := withIsolatedConfig(t)
	checker := func(url, key string) (string, error) { return "1.0", nil }

	code, _ := runWithInput(t, "newkey\nhttp://host:8686\nlidarrkey\ny\n", checker)
	if code != 0 {
		t.Fatalf("exit code = %d, want 0", code)
	}

	saved := config.ReadFile(filepath.Join(dir, "config.ini"))
	want := map[string]string{
		"acoustid_api_key": "newkey",
		"lidarr_url":       "http://host:8686",
		"lidarr_api_key":   "lidarrkey",
	}
	for k, v := range want {
		if saved[k] != v {
			t.Errorf("saved[%q] = %q, want %q", k, saved[k], v)
		}
	}
}

func TestBlankAnswersKeepExistingValues(t *testing.T) {
	dir := withIsolatedConfig(t)
	path := filepath.Join(dir, "config.ini")
	if err := config.SaveFile(map[string]string{"acoustid_api_key": "oldkey"}, path); err != nil {
		t.Fatal(err)
	}

	code, _ := runWithInput(t, "\nhttp://newhost:8686\n\ny\n", nil)
	if code != 0 {
		t.Fatalf("exit code = %d, want 0", code)
	}

	saved := config.ReadFile(path)
	if saved["acoustid_api_key"] != "oldkey" {
		t.Errorf("acoustid_api_key = %q, want oldkey", saved["acoustid_api_key"])
	}
	if saved["lidarr_url"] != "http://newhost:8686" {
		t.Errorf("lidarr_url = %q, want http://newhost:8686", saved["lidarr_url"])
	}
}

func TestDecliningSaveLeavesFileUntouched(t *testing.T) {
	dir := withIsolatedConfig(t)
	code, _ := runWithInput(t, "newkey\n\n\nn\n", nil)
	if code != 1 {
		t.Fatalf("exit code = %d, want 1", code)
	}
	if _, err := os.Stat(filepath.Join(dir, "config.ini")); err == nil {
		t.Error("expected config.ini not to be created")
	}
}

func TestNothingChangedSkipsWrite(t *testing.T) {
	dir := withIsolatedConfig(t)
	code, out := runWithInput(t, "\n\n\n", nil)
	if code != 0 {
		t.Fatalf("exit code = %d, want 0", code)
	}
	if !strings.Contains(out, "Nothing changed.") {
		t.Errorf("expected 'Nothing changed.' in output, got:\n%s", out)
	}
	if _, err := os.Stat(filepath.Join(dir, "config.ini")); err == nil {
		t.Error("expected config.ini not to be created")
	}
}

func TestEnvOverrideSkipsPromptAndIsNotSaved(t *testing.T) {
	dir := withIsolatedConfig(t)
	t.Setenv("LIDARR_URL", "http://env:8686")

	code, _ := runWithInput(t, "\nignored-because-env-wins\n\ny\n", nil)
	if code != 0 {
		t.Fatalf("exit code = %d, want 0", code)
	}

	saved := config.ReadFile(filepath.Join(dir, "config.ini"))
	if _, ok := saved["lidarr_url"]; ok {
		t.Errorf("lidarr_url should not be saved (env-sourced), got %q", saved["lidarr_url"])
	}
}

func TestVerifySkippedWhenLidarrIncomplete(t *testing.T) {
	var out bytes.Buffer
	p := &ioPrompter{in: bufio.NewReader(strings.NewReader("")), out: &out}
	if !verify(p, map[string]string{"lidarr_url": "http://host:8686"}, nil) {
		t.Error("verify() = false, want true (incomplete lidarr fields should skip)")
	}
	if !verify(p, map[string]string{}, nil) {
		t.Error("verify() = false, want true (no lidarr fields at all)")
	}
}

func TestVerifyPassesOnSuccessfulConnection(t *testing.T) {
	var out bytes.Buffer
	p := &ioPrompter{in: bufio.NewReader(strings.NewReader("")), out: &out}
	var gotURL, gotKey string
	checker := func(url, key string) (string, error) {
		gotURL, gotKey = url, key
		return "1.0", nil
	}
	merged := map[string]string{"lidarr_url": "http://host:8686", "lidarr_api_key": "key123"}
	if !verify(p, merged, checker) {
		t.Error("verify() = false, want true")
	}
	if gotURL != "http://host:8686" || gotKey != "key123" {
		t.Errorf("checker called with (%q, %q)", gotURL, gotKey)
	}
}

func TestVerifyFailsOnRejectedConnection(t *testing.T) {
	var out bytes.Buffer
	p := &ioPrompter{in: bufio.NewReader(strings.NewReader("")), out: &out}
	checker := func(url, key string) (string, error) { return "", errors.New("Lidarr rejected the API key") }
	merged := map[string]string{"lidarr_url": "http://host:8686", "lidarr_api_key": "wrongkey"}
	if verify(p, merged, checker) {
		t.Error("verify() = true, want false")
	}
}

func TestVerifyUsesEnvOverrideValue(t *testing.T) {
	t.Setenv("LIDARR_URL", "http://env:8686")
	var out bytes.Buffer
	p := &ioPrompter{in: bufio.NewReader(strings.NewReader("")), out: &out}
	var gotURL string
	checker := func(url, key string) (string, error) { gotURL = url; return "1.0", nil }
	merged := map[string]string{"lidarr_url": "http://file:8686", "lidarr_api_key": "key123"}
	if !verify(p, merged, checker) {
		t.Error("verify() = false, want true")
	}
	if gotURL != "http://env:8686" {
		t.Errorf("checker called with url=%q, want env override", gotURL)
	}
}

func TestFailedVerificationDefaultsToNotSaving(t *testing.T) {
	dir := withIsolatedConfig(t)
	checker := func(url, key string) (string, error) { return "", errors.New("Lidarr rejected the API key") }

	code, _ := runWithInput(t, "\nhttp://host:8686\nbadkey\n\n", checker)
	if code != 1 {
		t.Fatalf("exit code = %d, want 1", code)
	}
	if _, err := os.Stat(filepath.Join(dir, "config.ini")); err == nil {
		t.Error("expected config.ini not to be created")
	}
}

func TestFailedVerificationCanBeOverridden(t *testing.T) {
	dir := withIsolatedConfig(t)
	checker := func(url, key string) (string, error) { return "", errors.New("Lidarr rejected the API key") }

	code, _ := runWithInput(t, "\nhttp://host:8686\nbadkey\ny\n", checker)
	if code != 0 {
		t.Fatalf("exit code = %d, want 0", code)
	}

	saved := config.ReadFile(filepath.Join(dir, "config.ini"))
	if saved["lidarr_api_key"] != "badkey" {
		t.Errorf("lidarr_api_key = %q, want badkey", saved["lidarr_api_key"])
	}
}
