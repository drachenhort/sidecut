package acoustid

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/bogem/id3v2/v2"
)

func fixedFingerprint(duration int, fingerprint string, err error) func(string) (int, string, error) {
	return func(string) (int, string, error) { return duration, fingerprint, err }
}

// newAcoustIDServer routes requests by the `meta` query param, mirroring
// the real API's one-mode-per-request quirk: recordingsBody for
// meta=recordings, releasegroupsBody for meta=releasegroups.
func newAcoustIDServer(t *testing.T, recordingsBody, releasegroupsBody string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("meta") == "releasegroups" {
			w.Write([]byte(releasegroupsBody))
			return
		}
		w.Write([]byte(recordingsBody))
	}))
	t.Cleanup(srv.Close)
	return srv
}

// newMusicBrainzServer returns body for any recording ID lookup, unless
// byRecording is set, in which case it's routed by the recording ID in
// the URL path.
func newMusicBrainzServer(t *testing.T, body string, byRecording map[string]string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if byRecording != nil {
			id := strings.TrimPrefix(r.URL.Path, "/recording/")
			w.Write([]byte(byRecording[id]))
			return
		}
		w.Write([]byte(body))
	}))
	t.Cleanup(srv.Close)
	return srv
}

func newTestChecker(t *testing.T, fp func(string) (int, string, error), acoustidURL, musicbrainzURL string) *Checker {
	t.Helper()
	return &Checker{
		APIKey:             "fake-api-key",
		Fingerprint:        fp,
		HTTPClient:         http.DefaultClient,
		AcoustIDURL:        acoustidURL,
		MusicBrainzURL:     musicbrainzURL,
		acoustidLimiter:    newRateLimiter(1_000_000),
		musicbrainzLimiter: newRateLimiter(1_000_000),
	}
}

func emptyMusicBrainzServer(t *testing.T) *httptest.Server {
	return newMusicBrainzServer(t, `{"releases": []}`, nil)
}

func TestCheckReportsMatchForTaggedRecording(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, map[string]string{"musicbrainz_trackid": "mb-track-123"})
	acoustidSrv := newAcoustIDServer(t, `{"status":"ok","results":[{"id":"acoustid-1","score":0.95,"recordings":[{"id":"mb-track-123","title":"Song"}]}]}`, `{"status":"ok","results":[]}`)
	mbSrv := emptyMusicBrainzServer(t)
	c := newTestChecker(t, fixedFingerprint(245, "fp", nil), acoustidSrv.URL, mbSrv.URL)

	result := c.Check(path)

	if result.Status != "match" {
		t.Fatalf("status = %q, want match (%+v)", result.Status, result)
	}
	if result.RecordingID != "mb-track-123" {
		t.Errorf("recordingID = %q", result.RecordingID)
	}
}

func TestCheckReportsMismatchWhenTagDisagrees(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, map[string]string{
		"artist": "Wrong Artist", "title": "Wrong Title", "musicbrainz_trackid": "mb-track-wrong",
	})
	acoustidSrv := newAcoustIDServer(t,
		`{"status":"ok","results":[{"id":"acoustid-1","score":0.95,"recordings":[{"id":"mb-track-correct","title":"Song","artists":[{"name":"Artist"}]}]}]}`,
		`{"status":"ok","results":[]}`)
	mbSrv := emptyMusicBrainzServer(t)
	c := newTestChecker(t, fixedFingerprint(245, "fp", nil), acoustidSrv.URL, mbSrv.URL)

	result := c.Check(path)

	if result.Status != "mismatch" {
		t.Fatalf("status = %q, want mismatch (%+v)", result.Status, result)
	}
	if result.RecordingID != "mb-track-correct" {
		t.Errorf("recordingID = %q", result.RecordingID)
	}
	for _, want := range []string{"Wrong Artist - Wrong Title", "mb-track-wrong", "Artist - Song", "mb-track-correct"} {
		if !strings.Contains(result.Detail, want) {
			t.Errorf("detail %q missing %q", result.Detail, want)
		}
	}
}

func TestCheckReportsMismatchWithNoLinkedRecording(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, map[string]string{
		"artist": "Wrong Artist", "title": "Wrong Title", "musicbrainz_trackid": "mb-track-wrong",
	})
	acoustidSrv := newAcoustIDServer(t, `{"status":"ok","results":[{"id":"acoustid-1","score":0.6,"recordings":[]}]}`, `{"status":"ok","results":[]}`)
	mbSrv := emptyMusicBrainzServer(t)
	c := newTestChecker(t, fixedFingerprint(245, "fp", nil), acoustidSrv.URL, mbSrv.URL)

	result := c.Check(path)

	if result.Status != "mismatch" || result.RecordingID != "" {
		t.Fatalf("got %+v", result)
	}
	if !strings.Contains(result.Detail, "Wrong Artist - Wrong Title") {
		t.Errorf("detail = %q", result.Detail)
	}
}

func TestCheckReportsIdentifiedWhenUntagged(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, nil)
	acoustidSrv := newAcoustIDServer(t, `{"status":"ok","results":[{"id":"acoustid-1","score":0.9,"recordings":[{"id":"mb-track-1","title":"Song"}]}]}`, `{"status":"ok","results":[]}`)
	mbSrv := emptyMusicBrainzServer(t)
	c := newTestChecker(t, fixedFingerprint(245, "fp", nil), acoustidSrv.URL, mbSrv.URL)

	result := c.Check(path)

	if result.Status != "identified" || result.RecordingID != "mb-track-1" {
		t.Fatalf("got %+v", result)
	}
}

func TestCheckSurfacesReleaseTypeFromReleaseGroups(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, nil)
	acoustidSrv := newAcoustIDServer(t,
		`{"status":"ok","results":[{"id":"acoustid-1","score":0.9,"recordings":[{"id":"mb-track-1","title":"Song"}]}]}`,
		`{"status":"ok","results":[{"id":"acoustid-1","score":0.9,"releasegroups":[{"id":"rg-1","type":"Album"}]}]}`)
	mbSrv := emptyMusicBrainzServer(t)
	c := newTestChecker(t, fixedFingerprint(245, "fp", nil), acoustidSrv.URL, mbSrv.URL)

	result := c.Check(path)

	if result.ReleaseType != "album" {
		t.Errorf("releaseType = %q, want album (%+v)", result.ReleaseType, result)
	}
}

func TestCheckPrefersReleaseGroupMatchingTaggedAlbum(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, map[string]string{"album": "Greatest Hits"})
	acoustidSrv := newAcoustIDServer(t,
		`{"status":"ok","results":[{"id":"acoustid-1","score":0.9,"recordings":[{"id":"mb-track-1","title":"Song"}]}]}`,
		`{"status":"ok","results":[{"id":"acoustid-1","score":0.9,"releasegroups":[{"id":"rg-1","title":"Original Album","type":"Album"},{"id":"rg-2","title":"Greatest Hits","type":"Album","secondarytypes":["Compilation"]}]}]}`)
	mbSrv := emptyMusicBrainzServer(t)
	c := newTestChecker(t, fixedFingerprint(245, "fp", nil), acoustidSrv.URL, mbSrv.URL)

	result := c.Check(path)

	if result.ReleaseType != "compilation" {
		t.Errorf("releaseType = %q, want compilation", result.ReleaseType)
	}
}

func TestCheckSurfacesDateAndOriginalDateFromMusicBrainz(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, nil)
	acoustidSrv := newAcoustIDServer(t,
		`{"status":"ok","results":[{"id":"acoustid-1","score":0.9,"recordings":[{"id":"mb-track-1","title":"Song"}]}]}`,
		`{"status":"ok","results":[{"id":"acoustid-1","score":0.9}]}`)
	mbSrv := newMusicBrainzServer(t, `{"releases":[{"title":"Iron Man 2","date":"2011-06-01","release-group":{"primary-type":"Album","secondary-types":["Compilation"],"first-release-date":"1980-07-25"}}]}`, nil)
	c := newTestChecker(t, fixedFingerprint(245, "fp", nil), acoustidSrv.URL, mbSrv.URL)

	result := c.Check(path)

	if result.Date != "2011-06-01" || result.OriginalDate != "1980-07-25" || result.ReleaseType != "compilation" {
		t.Errorf("got %+v", result)
	}
}

func TestCheckProvenanceUsesTaggedRecordingNotFirstLinked(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, map[string]string{"musicbrainz_trackid": "mb-track-B"})
	acoustidSrv := newAcoustIDServer(t,
		`{"status":"ok","results":[{"id":"acoustid-1","score":0.9,"recordings":[{"id":"mb-track-A","title":"Song (Mono)"},{"id":"mb-track-B","title":"Song (Stereo)"}]}]}`,
		`{"status":"ok","results":[{"id":"acoustid-1","score":0.9}]}`)
	mbSrv := newMusicBrainzServer(t, "", map[string]string{
		"mb-track-A": `{"releases":[{"title":"Wrong Release","date":"1999-01-01","release-group":{"primary-type":"Compilation","first-release-date":"1999-01-01"}}]}`,
		"mb-track-B": `{"releases":[{"title":"Right Release","date":"1980-01-01","release-group":{"primary-type":"Album","first-release-date":"1980-01-01"}}]}`,
	})
	c := newTestChecker(t, fixedFingerprint(245, "fp", nil), acoustidSrv.URL, mbSrv.URL)

	result := c.Check(path)

	if result.Status != "match" || result.RecordingID != "mb-track-B" {
		t.Fatalf("got %+v", result)
	}
	if result.Date != "1980-01-01" || result.OriginalDate != "1980-01-01" || result.ReleaseType != "album" {
		t.Errorf("got %+v", result)
	}
}

func TestCheckReadsExistingTagsFromMP3(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.mp3")
	writeMP3Tags(t, path, "Artist", "Song", "", "mb-track-123")
	acoustidSrv := newAcoustIDServer(t, `{"status":"ok","results":[{"id":"acoustid-1","score":0.95,"recordings":[{"id":"mb-track-123","title":"Song"}]}]}`, `{"status":"ok","results":[]}`)
	mbSrv := emptyMusicBrainzServer(t)
	c := newTestChecker(t, fixedFingerprint(245, "fp", nil), acoustidSrv.URL, mbSrv.URL)

	result := c.Check(path)

	if result.Status != "match" || result.RecordingID != "mb-track-123" {
		t.Fatalf("got %+v", result)
	}
}

func TestCheckReportsNoMatch(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, nil)
	acoustidSrv := newAcoustIDServer(t, `{"status":"ok","results":[]}`, `{"status":"ok","results":[]}`)
	mbSrv := emptyMusicBrainzServer(t)
	c := newTestChecker(t, fixedFingerprint(245, "fp", nil), acoustidSrv.URL, mbSrv.URL)

	result := c.Check(path)

	if result.Status != "no_match" {
		t.Errorf("status = %q, want no_match", result.Status)
	}
}

func TestCheckReportsErrorOnFingerprintFailure(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, nil)
	c := newTestChecker(t, fixedFingerprint(0, "", errors.New("fpcalc not found")), "http://unused.invalid", "http://unused.invalid")

	result := c.Check(path)

	if result.Status != "error" {
		t.Errorf("status = %q, want error", result.Status)
	}
}

func TestCheckSurfacesAPIErrorMessageInsteadOfRaising(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, nil)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		w.Write([]byte(`{"status":"error","error":{"code":4,"message":"invalid API key"}}`))
	}))
	t.Cleanup(srv.Close)
	c := newTestChecker(t, fixedFingerprint(245, "fp", nil), srv.URL, "http://unused.invalid")

	result := c.Check(path)

	if result.Status != "error" {
		t.Fatalf("status = %q, want error", result.Status)
	}
	if result.Detail != "AcoustID error: invalid API key" {
		t.Errorf("detail = %q", result.Detail)
	}
}

func TestCheckReportsErrorOnRequestFailure(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, nil)
	c := newTestChecker(t, fixedFingerprint(245, "fp", nil), "http://127.0.0.1:1", "http://unused.invalid")

	result := c.Check(path)

	if result.Status != "error" {
		t.Errorf("status = %q, want error", result.Status)
	}
}

func TestCheckUsesTheSharedRateLimiter(t *testing.T) {
	path := filepath.Join(t.TempDir(), "song.flac")
	writeFLACTags(t, path, nil)
	acoustidSrv := newAcoustIDServer(t, `{"status":"ok","results":[]}`, `{"status":"ok","results":[]}`)
	mbSrv := emptyMusicBrainzServer(t)
	c := newTestChecker(t, fixedFingerprint(245, "fp", nil), acoustidSrv.URL, mbSrv.URL)
	c.acoustidLimiter = newRateLimiter(20) // min interval 50ms

	start := time.Now()
	c.Check(path)
	c.Check(path)
	elapsed := time.Since(start)

	if elapsed < 40*time.Millisecond {
		t.Errorf("elapsed = %v, want the second Check to have waited on the shared limiter", elapsed)
	}
}

func writeFLACTags(t *testing.T, path string, tags map[string]string) {
	t.Helper()
	requireFFmpeg(t)
	makeFLAC(t, path, tags)
}

func writeMP3Tags(t *testing.T, path, artist, title, album, recordingID string) {
	t.Helper()
	if err := os.WriteFile(path, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	tag, err := id3v2.Open(path, id3v2.Options{Parse: false})
	if err != nil {
		t.Fatal(err)
	}
	if artist != "" {
		tag.SetArtist(artist)
	}
	if title != "" {
		tag.SetTitle(title)
	}
	if album != "" {
		tag.SetAlbum(album)
	}
	if recordingID != "" {
		tag.AddUFIDFrame(id3v2.UFIDFrame{OwnerIdentifier: ufidOwner, Identifier: []byte(recordingID)})
	}
	if err := tag.Save(); err != nil {
		t.Fatal(err)
	}
}
