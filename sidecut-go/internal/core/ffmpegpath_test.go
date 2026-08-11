package core

import (
	"io"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestCheckFFmpegUsesConfiguredAbsolutePath(t *testing.T) {
	requireFFmpeg(t)
	resolved, err := exec.LookPath("ffmpeg")
	if err != nil {
		t.Fatal(err)
	}
	orig := FFmpegPath
	FFmpegPath = resolved
	t.Cleanup(func() { FFmpegPath = orig })

	if !CheckFFmpeg() {
		t.Error("CheckFFmpeg() = false, want true for a valid configured path")
	}
}

func TestCheckFFmpegFalseForBogusConfiguredPath(t *testing.T) {
	orig := FFmpegPath
	FFmpegPath = "/nonexistent/ffmpeg-binary-that-does-not-exist"
	t.Cleanup(func() { FFmpegPath = orig })

	if CheckFFmpeg() {
		t.Error("CheckFFmpeg() = true, want false for a bogus configured path")
	}
}

func TestConvertOneUsesConfiguredFFmpegPath(t *testing.T) {
	requireFFmpeg(t)
	resolved, err := exec.LookPath("ffmpeg")
	if err != nil {
		t.Fatal(err)
	}
	orig := FFmpegPath
	FFmpegPath = resolved
	t.Cleanup(func() { FFmpegPath = orig })

	dir := t.TempDir()
	src := filepath.Join(dir, "song.flac")
	makeFLAC(t, src, nil)

	result := ConvertOne(src, QualityPresets["v0"], io.Discard, nil, nil)
	if !result.OK {
		t.Fatalf("ConvertOne failed: %s", result.Message)
	}
}
