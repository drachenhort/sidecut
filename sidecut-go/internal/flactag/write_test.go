package flactag

import (
	"os/exec"
	"path/filepath"
	"testing"
)

func requireFFmpeg(t *testing.T) {
	t.Helper()
	if _, err := exec.LookPath("ffmpeg"); err != nil {
		t.Skip("ffmpeg not installed")
	}
}

func makeFLAC(t *testing.T, path string, tags map[string]string) {
	t.Helper()
	args := []string{"-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1"}
	for k, v := range tags {
		args = append(args, "-metadata", k+"="+v)
	}
	args = append(args, path)
	if out, err := exec.Command("ffmpeg", args...).CombinedOutput(); err != nil {
		t.Fatalf("ffmpeg failed: %v\n%s", err, out)
	}
}

func TestSetCommentsAddsNewKey(t *testing.T) {
	requireFFmpeg(t)
	path := filepath.Join(t.TempDir(), "song.flac")
	makeFLAC(t, path, map[string]string{"artist": "Artist"})

	if err := SetComments(path, []Comment{{Key: "releasetype", Value: "ep"}}); err != nil {
		t.Fatal(err)
	}

	tags, err := Read(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := tags.Get("releasetype"); len(got) != 1 || got[0] != "ep" {
		t.Errorf("releasetype = %v, want [ep]", got)
	}
	if got := tags.Get("artist"); len(got) != 1 || got[0] != "Artist" {
		t.Errorf("artist = %v, want [Artist] (must be preserved)", got)
	}
}

func TestSetCommentsReplacesExistingKeyCaseInsensitively(t *testing.T) {
	requireFFmpeg(t)
	path := filepath.Join(t.TempDir(), "song.flac")
	makeFLAC(t, path, map[string]string{"MUSICBRAINZ_TRACKID": "mb-track-wrong"})

	if err := SetComments(path, []Comment{{Key: "musicbrainz_trackid", Value: "mb-track-correct"}}); err != nil {
		t.Fatal(err)
	}

	tags, err := Read(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := tags.Get("musicbrainz_trackid"); len(got) != 1 || got[0] != "mb-track-correct" {
		t.Errorf("musicbrainz_trackid = %v, want [mb-track-correct]", got)
	}
}

func TestSetCommentsPreservesDuration(t *testing.T) {
	requireFFmpeg(t)
	path := filepath.Join(t.TempDir(), "song.flac")
	makeFLAC(t, path, nil)

	before, err := Read(path)
	if err != nil {
		t.Fatal(err)
	}

	if err := SetComments(path, []Comment{{Key: "date", Value: "2011-06-01"}}); err != nil {
		t.Fatal(err)
	}

	after, err := Read(path)
	if err != nil {
		t.Fatal(err)
	}
	if after.Duration != before.Duration {
		t.Errorf("duration changed: before %v, after %v", before.Duration, after.Duration)
	}
}

func TestSetCommentsSetsMultipleKeysInOneCall(t *testing.T) {
	requireFFmpeg(t)
	path := filepath.Join(t.TempDir(), "song.flac")
	makeFLAC(t, path, map[string]string{"date": "1980-07-25"})

	err := SetComments(path, []Comment{
		{Key: "originaldate", Value: "1980-07-25"},
	})
	if err != nil {
		t.Fatal(err)
	}

	tags, err := Read(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := tags.Get("date"); len(got) != 1 || got[0] != "1980-07-25" {
		t.Errorf("date = %v, want [1980-07-25] (untouched)", got)
	}
	if got := tags.Get("originaldate"); len(got) != 1 || got[0] != "1980-07-25" {
		t.Errorf("originaldate = %v, want [1980-07-25]", got)
	}
}
