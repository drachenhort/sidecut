package core

import (
	"bytes"
	"io"
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

// makeFLAC generates a tiny real FLAC file with the given ffmpeg
// -metadata key=value pairs, mirroring tests/test_core.py's make_flac.
func makeFLAC(t *testing.T, path string, tags map[string]string) {
	t.Helper()
	args := []string{"-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1"}
	for k, v := range tags {
		args = append(args, "-metadata", k+"="+v)
	}
	args = append(args, path)
	cmd := exec.Command("ffmpeg", args...)
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("ffmpeg failed: %v\n%s", err, out)
	}
}

func TestFindFLACFilesRecursiveCaseInsensitive(t *testing.T) {
	dir := t.TempDir()
	sub := filepath.Join(dir, "a")
	os.MkdirAll(sub, 0o755)
	os.WriteFile(filepath.Join(sub, "one.flac"), nil, 0o644)
	os.WriteFile(filepath.Join(sub, "two.FLAC"), nil, 0o644)
	os.WriteFile(filepath.Join(sub, "not_flac.mp3"), nil, 0o644)

	found, err := FindFLACFiles(dir)
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, p := range found {
		names = append(names, filepath.Base(p))
	}
	want := []string{"one.flac", "two.FLAC"}
	if len(names) != len(want) || names[0] != want[0] || names[1] != want[1] {
		t.Errorf("got %v, want %v", names, want)
	}
}

func TestFindFLACAndMP3FilesRecursiveCaseInsensitive(t *testing.T) {
	dir := t.TempDir()
	sub := filepath.Join(dir, "a")
	os.MkdirAll(sub, 0o755)
	os.WriteFile(filepath.Join(sub, "one.flac"), nil, 0o644)
	os.WriteFile(filepath.Join(sub, "two.MP3"), nil, 0o644)
	os.WriteFile(filepath.Join(sub, "not_audio.txt"), nil, 0o644)

	found, err := FindFLACAndMP3Files(dir)
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, p := range found {
		names = append(names, filepath.Base(p))
	}
	want := []string{"one.flac", "two.MP3"}
	if len(names) != len(want) || names[0] != want[0] || names[1] != want[1] {
		t.Errorf("got %v, want %v", names, want)
	}
}

func TestConvertOnePreservesStandardAndCustomTags(t *testing.T) {
	requireFFmpeg(t)
	dir := t.TempDir()
	src := filepath.Join(dir, "song.flac")
	makeFLAC(t, src, map[string]string{
		"artist":               "Test Artist",
		"album":                "Test Album",
		"title":                "Test Title",
		"ACOUSTID_ID":          "abcd-1234",
		"ACOUSTID_FINGERPRINT": "AQADfake",
		"MUSICBRAINZ_TRACKID":  "mb-track-123",
	})

	result := ConvertOne(src, QualityPresets["v0"], io.Discard, nil, nil)
	if !result.OK {
		t.Fatalf("conversion failed: %s", result.Message)
	}
	if _, err := os.Stat(src); err == nil {
		t.Error("expected source to be removed")
	}
	dst := filepath.Join(dir, "song.mp3")
	if _, err := os.Stat(dst); err != nil {
		t.Fatalf("expected %s to exist", dst)
	}

	tag, err := id3v2.Open(dst, id3v2.Options{Parse: true})
	if err != nil {
		t.Fatal(err)
	}
	defer tag.Close()

	if got := tag.GetTextFrame("TPE1").Text; got != "Test Artist" {
		t.Errorf("TPE1 = %q, want Test Artist", got)
	}
	if got := tag.GetTextFrame("TALB").Text; got != "Test Album" {
		t.Errorf("TALB = %q, want Test Album", got)
	}
	if got := tag.GetTextFrame("TIT2").Text; got != "Test Title" {
		t.Errorf("TIT2 = %q, want Test Title", got)
	}

	txxx := map[string]string{}
	for _, f := range tag.GetFrames(tag.CommonID("TXXX")) {
		if udtf, ok := f.(id3v2.UserDefinedTextFrame); ok {
			txxx[udtf.Description] = udtf.Value
		}
	}
	if txxx["Acoustid Id"] != "abcd-1234" {
		t.Errorf("Acoustid Id TXXX = %q, want abcd-1234", txxx["Acoustid Id"])
	}
	if txxx["Acoustid Fingerprint"] != "AQADfake" {
		t.Errorf("Acoustid Fingerprint TXXX = %q, want AQADfake", txxx["Acoustid Fingerprint"])
	}

	ufids := tag.GetFrames(tag.CommonID("Unique file identifier"))
	if len(ufids) != 1 {
		t.Fatalf("expected 1 UFID frame, got %d", len(ufids))
	}
	if ufid, ok := ufids[0].(id3v2.UFIDFrame); !ok || string(ufid.Identifier) != "mb-track-123" {
		t.Errorf("UFID = %+v, want identifier mb-track-123", ufids[0])
	}
}

func TestConvertOnePreservesEmbeddedCoverArt(t *testing.T) {
	requireFFmpeg(t)
	dir := t.TempDir()
	src := filepath.Join(dir, "song.flac")
	makeFLAC(t, src, nil)

	// Embed a picture with ffmpeg's -attach. Unlike the Python test (which
	// uses mutagen's FLAC.add_picture and doesn't care whether the bytes
	// are a real image), ffmpeg validates the image when attaching it to
	// a FLAC, so a fake/truncated PNG gets rejected - generate a real
	// (if tiny) one instead.
	coverPath := filepath.Join(dir, "cover.png")
	genCoverCmd := exec.Command("ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
		"-i", "color=c=red:s=2x2", "-frames:v", "1", coverPath)
	if out, err := genCoverCmd.CombinedOutput(); err != nil {
		t.Fatalf("ffmpeg cover generation failed: %v\n%s", err, out)
	}
	coverBytes, err := os.ReadFile(coverPath)
	if err != nil {
		t.Fatal(err)
	}
	withCover := filepath.Join(dir, "song-with-cover.flac")
	cmd := exec.Command("ffmpeg", "-y", "-loglevel", "error", "-i", src, "-i", coverPath,
		"-map", "0:a", "-map", "1", "-c", "copy", "-disposition:1", "attached_pic", withCover)
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("ffmpeg attach failed: %v\n%s", err, out)
	}

	result := ConvertOne(withCover, QualityPresets["v0"], io.Discard, nil, nil)
	if !result.OK {
		t.Fatalf("conversion failed: %s", result.Message)
	}

	dst := filepath.Join(dir, "song-with-cover.mp3")
	tag, err := id3v2.Open(dst, id3v2.Options{Parse: true})
	if err != nil {
		t.Fatal(err)
	}
	defer tag.Close()

	pics := tag.GetFrames(tag.CommonID("Attached picture"))
	if len(pics) != 1 {
		t.Fatalf("expected 1 APIC frame, got %d", len(pics))
	}
	pic, ok := pics[0].(id3v2.PictureFrame)
	if !ok || !bytes.Equal(pic.Picture, coverBytes) {
		t.Errorf("APIC picture data mismatch")
	}
}

func TestConvertOneReportsProgress(t *testing.T) {
	requireFFmpeg(t)
	dir := t.TempDir()
	src := filepath.Join(dir, "song.flac")
	makeFLAC(t, src, nil)

	var percents []float64
	result := ConvertOne(src, QualityPresets["v0"], io.Discard, func(p Progress) {
		percents = append(percents, p.Percent)
	}, nil)

	if !result.OK {
		t.Fatalf("conversion failed: %s", result.Message)
	}
	if len(percents) == 0 {
		t.Fatal("expected at least one progress update")
	}
	if last := percents[len(percents)-1]; last < 90.0 {
		t.Errorf("last progress = %v, want >= 90", last)
	}
}

func TestConvertOneKeepsSourceOnFailure(t *testing.T) {
	requireFFmpeg(t)
	dir := t.TempDir()
	src := filepath.Join(dir, "broken.flac")
	os.WriteFile(src, []byte("not a real flac file"), 0o644)

	result := ConvertOne(src, QualityPresets["v0"], io.Discard, nil, nil)
	if result.OK {
		t.Fatal("expected conversion to fail")
	}
	if _, err := os.Stat(src); err != nil {
		t.Error("expected source to still exist")
	}
	if _, err := os.Stat(filepath.Join(dir, "broken.mp3")); err == nil {
		t.Error("expected no broken.mp3 to be created")
	}
}

func TestConvertOnePreservesBothDateAndYear(t *testing.T) {
	requireFFmpeg(t)
	dir := t.TempDir()
	src := filepath.Join(dir, "song.flac")
	makeFLAC(t, src, map[string]string{"date": "2019-05-01", "year": "1999"})

	result := ConvertOne(src, QualityPresets["v0"], io.Discard, nil, nil)
	if !result.OK {
		t.Fatalf("conversion failed: %s", result.Message)
	}

	dst := filepath.Join(dir, "song.mp3")
	tag, err := id3v2.Open(dst, id3v2.Options{Parse: true})
	if err != nil {
		t.Fatal(err)
	}
	defer tag.Close()

	if got := tag.GetTextFrame("TDRC").Text; got != "2019-05-01" {
		t.Errorf("TDRC = %q, want 2019-05-01", got)
	}
	for _, f := range tag.GetFrames(tag.CommonID("TXXX")) {
		if udtf, ok := f.(id3v2.UserDefinedTextFrame); ok && udtf.Description == "year" {
			if udtf.Value != "1999" {
				t.Errorf("year TXXX = %q, want 1999", udtf.Value)
			}
		}
	}
}

func TestConvertOneReportsFailureInsteadOfRaisingOnMissingSource(t *testing.T) {
	dir := t.TempDir()
	src := filepath.Join(dir, "gone.flac") // never created

	result := ConvertOne(src, QualityPresets["v0"], io.Discard, nil, nil)
	if result.OK {
		t.Fatal("expected conversion to fail")
	}
	if _, err := os.Stat(filepath.Join(dir, "gone.mp3")); err == nil {
		t.Error("expected no gone.mp3 to be created")
	}
}
