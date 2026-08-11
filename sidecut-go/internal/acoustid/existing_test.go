package acoustid

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/bogem/id3v2/v2"
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

func TestReadExistingRecordingFromFLAC(t *testing.T) {
	requireFFmpeg(t)
	path := filepath.Join(t.TempDir(), "song.flac")
	makeFLAC(t, path, map[string]string{
		"MUSICBRAINZ_TRACKID": "mb-track-123",
		"artist":              "Artist",
		"title":               "Title",
		"album":               "Album",
	})

	got, err := readExistingRecording(path)

	if err != nil {
		t.Fatal(err)
	}
	if got.recordingID != "mb-track-123" || got.artist != "Artist" || got.title != "Title" || got.album != "Album" {
		t.Errorf("got %+v", got)
	}
}

func TestReadExistingRecordingFromFLACWithoutTag(t *testing.T) {
	requireFFmpeg(t)
	path := filepath.Join(t.TempDir(), "song.flac")
	makeFLAC(t, path, nil)

	got, err := readExistingRecording(path)

	if err != nil {
		t.Fatal(err)
	}
	if got.recordingID != "" {
		t.Errorf("recordingID = %q, want empty", got.recordingID)
	}
}

func TestReadExistingRecordingFromMP3(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.mp3")
	if err := os.WriteFile(path, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	tag, err := id3v2.Open(path, id3v2.Options{Parse: false})
	if err != nil {
		t.Fatal(err)
	}
	tag.SetArtist("Artist")
	tag.SetTitle("Title")
	tag.SetAlbum("Album")
	tag.AddUFIDFrame(id3v2.UFIDFrame{OwnerIdentifier: ufidOwner, Identifier: []byte("mb-track-123")})
	if err := tag.Save(); err != nil {
		t.Fatal(err)
	}

	got, err := readExistingRecording(path)

	if err != nil {
		t.Fatal(err)
	}
	if got.recordingID != "mb-track-123" || got.artist != "Artist" || got.title != "Title" || got.album != "Album" {
		t.Errorf("got %+v", got)
	}
}
