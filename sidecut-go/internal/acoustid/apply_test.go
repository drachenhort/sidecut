package acoustid

import (
	"path/filepath"
	"testing"

	"github.com/bogem/id3v2/v2"

	"sidecut-go/internal/flactag"
)

func TestApplyReleaseTypeWritesMissingTag(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, nil)
	check := Check{Status: "identified", ReleaseType: "ep"}

	applied, err := ApplyReleaseType(path, check)

	if err != nil {
		t.Fatal(err)
	}
	if !applied {
		t.Fatal("want applied")
	}
	tags, err := flactag.Read(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := tags.Get("releasetype"); len(got) != 1 || got[0] != "ep" {
		t.Errorf("releasetype = %v", got)
	}
}

func TestApplyReleaseTypeNeverOverwritesExistingTag(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, map[string]string{"releasetype": "single"})
	check := Check{Status: "identified", ReleaseType: "album"}

	applied, err := ApplyReleaseType(path, check)

	if err != nil {
		t.Fatal(err)
	}
	if applied {
		t.Fatal("want not applied")
	}
	tags, err := flactag.Read(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := tags.Get("releasetype"); len(got) != 1 || got[0] != "single" {
		t.Errorf("releasetype = %v, want unchanged [single]", got)
	}
}

func TestApplyReleaseTypeSkipsWhenNoReleaseType(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, nil)
	check := Check{Status: "no_match"}

	applied, err := ApplyReleaseType(path, check)

	if err != nil {
		t.Fatal(err)
	}
	if applied {
		t.Fatal("want not applied")
	}
}

func TestApplyReleaseTypeWritesMissingTagOnMP3(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.mp3")
	writeMP3Tags(t, path, "Artist", "Song", "", "")
	check := Check{Status: "identified", ReleaseType: "ep"}

	applied, err := ApplyReleaseType(path, check)

	if err != nil {
		t.Fatal(err)
	}
	if !applied {
		t.Fatal("want applied")
	}
	tag, err := id3v2.Open(path, id3v2.Options{Parse: true})
	if err != nil {
		t.Fatal(err)
	}
	defer tag.Close()
	if got := findTXXX(tag, releaseTypeDesc); got != "ep" {
		t.Errorf("TXXX:%s = %q, want ep", releaseTypeDesc, got)
	}
}

func TestApplyReleaseTypeNeverOverwritesExistingTagOnMP3(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.mp3")
	writeMP3Tags(t, path, "Artist", "Song", "", "")
	tag, err := id3v2.Open(path, id3v2.Options{Parse: true})
	if err != nil {
		t.Fatal(err)
	}
	tag.AddUserDefinedTextFrame(id3v2.UserDefinedTextFrame{Encoding: id3v2.EncodingUTF8, Description: releaseTypeDesc, Value: "single"})
	tag.SetVersion(3)
	if err := tag.Save(); err != nil {
		t.Fatal(err)
	}
	check := Check{Status: "identified", ReleaseType: "album"}

	applied, err := ApplyReleaseType(path, check)

	if err != nil {
		t.Fatal(err)
	}
	if applied {
		t.Fatal("want not applied")
	}
	verify, err := id3v2.Open(path, id3v2.Options{Parse: true})
	if err != nil {
		t.Fatal(err)
	}
	defer verify.Close()
	if got := findTXXX(verify, releaseTypeDesc); got != "single" {
		t.Errorf("TXXX:%s = %q, want unchanged single", releaseTypeDesc, got)
	}
}

func TestApplyReleaseProvenanceWritesMissingTags(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, nil)
	check := Check{Status: "identified", RecordingID: "mb-track-1", Date: "2011-06-01", OriginalDate: "1980-07-25"}

	applied, err := ApplyReleaseProvenance(path, check)

	if err != nil {
		t.Fatal(err)
	}
	if !applied {
		t.Fatal("want applied")
	}
	tags, err := flactag.Read(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := tags.Get("date"); len(got) != 1 || got[0] != "2011-06-01" {
		t.Errorf("date = %v", got)
	}
	if got := tags.Get("originaldate"); len(got) != 1 || got[0] != "1980-07-25" {
		t.Errorf("originaldate = %v", got)
	}
}

func TestApplyReleaseProvenanceNeverOverwritesExistingTags(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, map[string]string{"date": "1980-07-25", "originaldate": "1980-07-25"})
	check := Check{Status: "identified", Date: "2011-06-01", OriginalDate: "1999-01-01"}

	applied, err := ApplyReleaseProvenance(path, check)

	if err != nil {
		t.Fatal(err)
	}
	if applied {
		t.Fatal("want not applied")
	}
	tags, err := flactag.Read(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := tags.Get("date"); got[0] != "1980-07-25" {
		t.Errorf("date = %v", got)
	}
}

func TestApplyReleaseProvenanceFillsOnlyMissingField(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, map[string]string{"date": "1980-07-25"})
	check := Check{Status: "identified", Date: "2011-06-01", OriginalDate: "1999-01-01"}

	applied, err := ApplyReleaseProvenance(path, check)

	if err != nil {
		t.Fatal(err)
	}
	if !applied {
		t.Fatal("want applied")
	}
	tags, err := flactag.Read(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := tags.Get("date"); got[0] != "1980-07-25" {
		t.Errorf("date = %v, want untouched", got)
	}
	if got := tags.Get("originaldate"); got[0] != "1999-01-01" {
		t.Errorf("originaldate = %v, want filled in", got)
	}
}

func TestApplyReleaseProvenanceSkipsWhenNoProvenance(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, nil)
	check := Check{Status: "no_match"}

	applied, err := ApplyReleaseProvenance(path, check)

	if err != nil {
		t.Fatal(err)
	}
	if applied {
		t.Fatal("want not applied")
	}
}

func TestApplyReleaseProvenanceWritesMissingTagsOnMP3(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.mp3")
	writeMP3Tags(t, path, "Artist", "Song", "", "")
	check := Check{Status: "identified", Date: "2011-06-01", OriginalDate: "1980-07-25"}

	applied, err := ApplyReleaseProvenance(path, check)

	if err != nil {
		t.Fatal(err)
	}
	if !applied {
		t.Fatal("want applied")
	}
	tag, err := id3v2.Open(path, id3v2.Options{Parse: true})
	if err != nil {
		t.Fatal(err)
	}
	defer tag.Close()
	if got := tag.GetTextFrame("TDRC").Text; got != "2011-06-01" {
		t.Errorf("TDRC = %q", got)
	}
	if got := tag.GetTextFrame("TDOR").Text; got != "1980-07-25" {
		t.Errorf("TDOR = %q", got)
	}
}

func TestApplyReleaseProvenanceNeverOverwritesExistingTagsOnMP3(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.mp3")
	writeMP3Tags(t, path, "Artist", "Song", "", "")
	tag, err := id3v2.Open(path, id3v2.Options{Parse: true})
	if err != nil {
		t.Fatal(err)
	}
	tag.AddTextFrame("TDRC", id3v2.EncodingUTF8, "1980-07-25")
	tag.SetVersion(3)
	if err := tag.Save(); err != nil {
		t.Fatal(err)
	}
	check := Check{Status: "identified", Date: "2011-06-01", OriginalDate: "1999-01-01"}

	applied, err := ApplyReleaseProvenance(path, check)

	if err != nil {
		t.Fatal(err)
	}
	if !applied {
		t.Fatal("want applied (originaldate still gets filled in)")
	}
	verify, err := id3v2.Open(path, id3v2.Options{Parse: true})
	if err != nil {
		t.Fatal(err)
	}
	defer verify.Close()
	if got := verify.GetTextFrame("TDRC").Text; got != "1980-07-25" {
		t.Errorf("TDRC = %q, want unchanged", got)
	}
	if got := verify.GetTextFrame("TDOR").Text; got != "1999-01-01" {
		t.Errorf("TDOR = %q, want filled in", got)
	}
}

func TestCorrectAcoustIDMismatchRewritesRecordingID(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, map[string]string{"artist": "Wrong Artist", "musicbrainz_trackid": "mb-track-wrong"})
	check := &Check{Status: "mismatch", Detail: "detail", RecordingID: "mb-track-correct", Score: 0.9, HasScore: true}

	corrected, err := CorrectAcoustIDMismatch(path, check, AutocorrectMinScore)

	if err != nil {
		t.Fatal(err)
	}
	if !corrected {
		t.Fatal("want corrected")
	}
	if !check.Corrected {
		t.Error("check.Corrected = false, want true")
	}
	if check.Detail != "detail — corrected musicbrainz_trackid tag" {
		t.Errorf("detail = %q", check.Detail)
	}
	tags, err := flactag.Read(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := tags.Get("musicbrainz_trackid"); len(got) != 1 || got[0] != "mb-track-correct" {
		t.Errorf("musicbrainz_trackid = %v", got)
	}
}

func TestCorrectAcoustIDMismatchSkipsLowConfidenceScore(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, map[string]string{"musicbrainz_trackid": "mb-track-wrong"})
	check := &Check{Status: "mismatch", RecordingID: "mb-track-correct", Score: 0.2, HasScore: true}

	corrected, err := CorrectAcoustIDMismatch(path, check, AutocorrectMinScore)

	if err != nil {
		t.Fatal(err)
	}
	if corrected || check.Corrected {
		t.Fatal("want not corrected")
	}
	tags, _ := flactag.Read(path)
	if got := tags.Get("musicbrainz_trackid"); got[0] != "mb-track-wrong" {
		t.Errorf("musicbrainz_trackid = %v, want unchanged", got)
	}
}

func TestCorrectAcoustIDMismatchSkipsNonMismatchStatus(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, map[string]string{"musicbrainz_trackid": "mb-track-1"})
	check := &Check{Status: "identified", RecordingID: "mb-track-2", Score: 0.9, HasScore: true}

	corrected, err := CorrectAcoustIDMismatch(path, check, AutocorrectMinScore)

	if err != nil {
		t.Fatal(err)
	}
	if corrected {
		t.Fatal("want not corrected")
	}
	tags, _ := flactag.Read(path)
	if got := tags.Get("musicbrainz_trackid"); got[0] != "mb-track-1" {
		t.Errorf("musicbrainz_trackid = %v, want unchanged", got)
	}
}

func TestCorrectAcoustIDMismatchSkipsWhenNoRecordingID(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, map[string]string{"musicbrainz_trackid": "mb-track-1"})
	check := &Check{Status: "mismatch", RecordingID: "", Score: 0.9, HasScore: true}

	corrected, err := CorrectAcoustIDMismatch(path, check, AutocorrectMinScore)

	if err != nil {
		t.Fatal(err)
	}
	if corrected {
		t.Fatal("want not corrected")
	}
}

func TestCorrectAcoustIDMismatchNeverTouchesMP3(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.mp3")
	writeMP3Tags(t, path, "Artist", "Song", "", "mb-track-wrong")
	check := &Check{Status: "mismatch", RecordingID: "mb-track-correct", Score: 0.9, HasScore: true}

	corrected, err := CorrectAcoustIDMismatch(path, check, AutocorrectMinScore)

	if err != nil {
		t.Fatal(err)
	}
	if corrected || check.Corrected {
		t.Fatal("want not corrected")
	}
}
