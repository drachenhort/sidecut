package config

import (
	"os"
	"path/filepath"
	"testing"
)

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestMissingFileReturnsEmpty(t *testing.T) {
	path := filepath.Join(t.TempDir(), "nope.ini")
	got := LoadSettings(path)
	if len(got) != 0 {
		t.Errorf("got %v, want empty", got)
	}
}

func TestMalformedIniReturnsEmpty(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.ini")
	writeFile(t, path, "not [ valid ini\nrandom garbage with no key=value structure at all here")
	got := LoadSettings(path)
	if len(got) != 0 {
		t.Errorf("got %v, want empty", got)
	}
}

func TestReadsFieldsFromFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.ini")
	writeFile(t, path, "[flac2mp3]\n"+
		"acoustid_api_key = filekey\n"+
		"lidarr_url = http://file:8686\n"+
		"lidarr_api_key = filelidarrkey\n")

	got := LoadSettings(path)
	want := map[string]string{
		"acoustid_api_key": "filekey",
		"lidarr_url":       "http://file:8686",
		"lidarr_api_key":   "filelidarrkey",
	}
	if len(got) != len(want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	for k, v := range want {
		if got[k] != v {
			t.Errorf("got[%q] = %q, want %q", k, got[k], v)
		}
	}
}

func TestEnvVarOverridesFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.ini")
	writeFile(t, path, "[flac2mp3]\nacoustid_api_key = filekey\n")
	t.Setenv("ACOUSTID_API_KEY", "envkey")

	got := LoadSettings(path)
	if got["acoustid_api_key"] != "envkey" {
		t.Errorf("acoustid_api_key = %q, want envkey", got["acoustid_api_key"])
	}
}

func TestPrecedenceOrderFullStack(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.ini")
	writeFile(t, path, "[flac2mp3]\n"+
		"acoustid_api_key = filekey\n"+
		"lidarr_url = http://file:8686\n"+
		"lidarr_api_key = filelidarrkey\n")
	t.Setenv("LIDARR_API_KEY", "envlidarrkey")

	got := LoadSettings(path)
	if got["acoustid_api_key"] != "filekey" {
		t.Errorf("acoustid_api_key = %q, want filekey", got["acoustid_api_key"])
	}
	if got["lidarr_url"] != "http://file:8686" {
		t.Errorf("lidarr_url = %q, want http://file:8686", got["lidarr_url"])
	}
	if got["lidarr_api_key"] != "envlidarrkey" {
		t.Errorf("lidarr_api_key = %q, want envlidarrkey", got["lidarr_api_key"])
	}
}

func TestReadFileIgnoresBlankFields(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.ini")
	writeFile(t, path, "[flac2mp3]\nlidarr_url = http://file:8686\nlidarr_api_key =\n")

	got := ReadFile(path)
	if _, ok := got["lidarr_api_key"]; ok {
		t.Errorf("blank lidarr_api_key should be absent, got %v", got)
	}
	if got["lidarr_url"] != "http://file:8686" {
		t.Errorf("lidarr_url = %q, want http://file:8686", got["lidarr_url"])
	}
}

func TestSaveFileRoundTrips(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.ini")
	if err := SaveFile(map[string]string{"acoustid_api_key": "newkey"}, path); err != nil {
		t.Fatal(err)
	}
	got := ReadFile(path)
	if got["acoustid_api_key"] != "newkey" {
		t.Errorf("acoustid_api_key = %q, want newkey", got["acoustid_api_key"])
	}
}

func TestSaveFilePreservesUntouchedFields(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.ini")
	if err := SaveFile(map[string]string{"acoustid_api_key": "oldkey"}, path); err != nil {
		t.Fatal(err)
	}
	if err := SaveFile(map[string]string{"lidarr_url": "http://newhost:8686"}, path); err != nil {
		t.Fatal(err)
	}
	got := ReadFile(path)
	if got["acoustid_api_key"] != "oldkey" {
		t.Errorf("acoustid_api_key = %q, want oldkey (should survive untouched)", got["acoustid_api_key"])
	}
	if got["lidarr_url"] != "http://newhost:8686" {
		t.Errorf("lidarr_url = %q, want http://newhost:8686", got["lidarr_url"])
	}
}

func TestSaveFileCreatesParentDir(t *testing.T) {
	path := filepath.Join(t.TempDir(), "nested", "dir", "config.ini")
	if err := SaveFile(map[string]string{"lidarr_url": "http://host:8686"}, path); err != nil {
		t.Fatal(err)
	}
	if !isFile(path) {
		t.Fatalf("expected %s to exist", path)
	}
}

func TestResolveConfigPathPrefersScriptDirWhenFileExists(t *testing.T) {
	dir := t.TempDir()
	local := filepath.Join(dir, "config.ini")
	writeFile(t, local, "[flac2mp3]\n")

	restore := setArgv0(filepath.Join(dir, "sidecut"))
	defer restore()

	if got := ResolveConfigPath(); got != local {
		t.Errorf("ResolveConfigPath() = %q, want %q", got, local)
	}
}

func TestResolveConfigPathDefaultsToScriptDirOnFirstRun(t *testing.T) {
	dir := t.TempDir()
	restore := setArgv0(filepath.Join(dir, "sidecut"))
	defer restore()
	// Isolate ConfigPath() too, not just ScriptDir(): without this, the
	// test only passes because the machine running it happens to have no
	// real ~/.config/flac2mp3/config.ini - a false negative waiting to
	// happen on any machine (or CI image) that does.
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(t.TempDir(), "empty-home-config"))

	want := filepath.Join(dir, "config.ini")
	if got := ResolveConfigPath(); got != want {
		t.Errorf("ResolveConfigPath() = %q, want %q", got, want)
	}
}

func TestResolveConfigPathPrefersExistingConfigPathOverFreshLocal(t *testing.T) {
	dir := t.TempDir()
	restore := setArgv0(filepath.Join(dir, "sidecut"))
	defer restore()

	xdgHome := filepath.Join(t.TempDir(), "home-config")
	t.Setenv("XDG_CONFIG_HOME", xdgHome)
	existing := filepath.Join(xdgHome, "flac2mp3", "config.ini")
	writeFile(t, existing, "[flac2mp3]\n")

	if got := ResolveConfigPath(); got != existing {
		t.Errorf("ResolveConfigPath() = %q, want %q (must not silently prefer an empty fresh local file)", got, existing)
	}
}

// setArgv0 points os.Args[0] at a path inside an isolated temp dir so
// ScriptDir()/ResolveConfigPath() never touch the real binary's directory
// during tests. Returns a restore func.
func setArgv0(path string) func() {
	orig := os.Args[0]
	os.Args[0] = path
	return func() { os.Args[0] = orig }
}
