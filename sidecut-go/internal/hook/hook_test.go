package hook

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"sidecut-go/internal/config"
)

func requireFFmpeg(t *testing.T) {
	t.Helper()
	if _, err := exec.LookPath("ffmpeg"); err != nil {
		t.Skip("ffmpeg not installed")
	}
}

func makeFLAC(t *testing.T, path string) {
	t.Helper()
	cmd := exec.Command("ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
		"-i", "sine=frequency=440:duration=1", "-metadata", "artist=Test Artist", path)
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("ffmpeg failed: %v\n%s", err, out)
	}
}

// isolateConfig keeps RunFromEnvironment's config.LoadSettings("") call
// away from the real ~/.config/flac2mp3/config.ini and the real binary's
// directory, same pattern as internal/configureui's tests.
func isolateConfig(t *testing.T) {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(dir, "empty-home-config"))
}

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func filepathStat(path string) (os.FileInfo, error) {
	return os.Stat(path)
}

func TestIsInvocationDetectsLidarrEventtype(t *testing.T) {
	orig, wasSet := os.LookupEnv("lidarr_eventtype")
	t.Cleanup(func() {
		if wasSet {
			os.Setenv("lidarr_eventtype", orig)
		} else {
			os.Unsetenv("lidarr_eventtype")
		}
	})

	os.Unsetenv("lidarr_eventtype")
	if IsInvocation() {
		t.Error("IsInvocation() = true, want false when lidarr_eventtype unset")
	}
	os.Setenv("lidarr_eventtype", "Download")
	if !IsInvocation() {
		t.Error("IsInvocation() = false, want true when lidarr_eventtype set")
	}
}

func TestRunFromEnvironmentHandlesTestEvent(t *testing.T) {
	isolateConfig(t)
	t.Setenv("lidarr_eventtype", "Test")
	if got := RunFromEnvironment("v0"); got != 0 {
		t.Errorf("got %d, want 0", got)
	}
}

func TestRunFromEnvironmentIgnoresUnsupportedEvent(t *testing.T) {
	isolateConfig(t)
	t.Setenv("lidarr_eventtype", "Grab")
	if got := RunFromEnvironment("v0"); got != 0 {
		t.Errorf("got %d, want 0", got)
	}
}

func TestRunFromEnvironmentErrorsWithoutTrackPaths(t *testing.T) {
	isolateConfig(t)
	t.Setenv("lidarr_eventtype", "Download")
	t.Setenv("lidarr_addedtrackpaths", "")
	t.Setenv("lidarr_trackfile_path", "")
	if got := RunFromEnvironment("v0"); got != 1 {
		t.Errorf("got %d, want 1", got)
	}
}

func TestRunFromEnvironmentDoesNothingForNonFLACTracks(t *testing.T) {
	isolateConfig(t)
	dir := t.TempDir()
	t.Setenv("lidarr_eventtype", "Download")
	t.Setenv("lidarr_addedtrackpaths", filepath.Join(dir, "already.mp3"))
	if got := RunFromEnvironment("v0"); got != 0 {
		t.Errorf("got %d, want 0", got)
	}
}

func TestRunFromEnvironmentConvertsWhenUnconfigured(t *testing.T) {
	requireFFmpeg(t)
	isolateConfig(t)
	dir := t.TempDir()
	flac := filepath.Join(dir, "song.flac")
	makeFLAC(t, flac)
	t.Setenv("lidarr_eventtype", "Download")
	t.Setenv("lidarr_addedtrackpaths", flac)
	t.Setenv("lidarr_artist_path", dir)

	if got := RunFromEnvironment("v0"); got != 0 {
		t.Errorf("got %d, want 0", got)
	}
	if _, err := filepathStat(flac); err == nil {
		t.Error("expected source flac to be removed")
	}
	if _, err := filepathStat(filepath.Join(dir, "song.mp3")); err != nil {
		t.Error("expected song.mp3 to exist")
	}
}

func TestRunFromEnvironmentReturnsErrorOnConversionFailure(t *testing.T) {
	requireFFmpeg(t)
	isolateConfig(t)
	dir := t.TempDir()
	broken := filepath.Join(dir, "broken.flac")
	writeFile(t, broken, "not a real flac")
	t.Setenv("lidarr_eventtype", "Download")
	t.Setenv("lidarr_addedtrackpaths", broken)
	t.Setenv("lidarr_artist_path", dir)

	if got := RunFromEnvironment("v0"); got != 1 {
		t.Errorf("got %d, want 1", got)
	}
}

func TestRunFromEnvironmentFallsBackToTrackfilePathEnvVar(t *testing.T) {
	requireFFmpeg(t)
	isolateConfig(t)
	dir := t.TempDir()
	flac := filepath.Join(dir, "song.flac")
	makeFLAC(t, flac)
	t.Setenv("lidarr_eventtype", "Download")
	t.Setenv("lidarr_addedtrackpaths", "")
	t.Setenv("lidarr_trackfile_path", flac)
	t.Setenv("lidarr_artist_path", dir)

	if got := RunFromEnvironment("v0"); got != 0 {
		t.Errorf("got %d, want 0", got)
	}
	if _, err := filepathStat(filepath.Join(dir, "song.mp3")); err != nil {
		t.Error("expected song.mp3 to exist")
	}
}

func TestConfigLoadSettingsDoesNotPanicWhenUnconfigured(t *testing.T) {
	isolateConfig(t)
	// Sanity check that RunFromEnvironment's call to config.LoadSettings("")
	// behaves the same as everywhere else in the port.
	got := config.LoadSettings("")
	if len(got) != 0 {
		t.Errorf("got %v, want empty in an isolated/unconfigured environment", got)
	}
}
