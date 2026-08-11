package lidarr

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// sequencedServer replies to successive requests with successive handlers,
// in order - the Go equivalent of Python's patch("requests.get",
// side_effect=[...]).
func sequencedServer(t *testing.T, handlers ...func(w http.ResponseWriter, r *http.Request)) *httptest.Server {
	t.Helper()
	i := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if i >= len(handlers) {
			t.Fatalf("unexpected extra request #%d: %s %s", i+1, r.Method, r.URL)
		}
		handlers[i](w, r)
		i++
	}))
	t.Cleanup(srv.Close)
	return srv
}

func jsonHandler(body string) func(http.ResponseWriter, *http.Request) {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(body))
	}
}

func matchedCandidateJSON(path string, artistID, albumID, trackID int) string {
	return fmt.Sprintf(`{"path":%q,"artist":{"id":%d},"album":{"id":%d},"albumReleaseId":99,"tracks":[{"id":%d}],"quality":{"id":4},"rejections":[]}`, path, artistID, albumID, trackID)
}

func noArtistMatchHandler() func(http.ResponseWriter, *http.Request) {
	return jsonHandler(`[]`)
}

func TestIsFullyMatched(t *testing.T) {
	matched := ImportCandidate{Artist: &IDRef{ID: 1}, Album: &IDRef{ID: 2}, Tracks: []IDRef{{ID: 3}}}
	if !IsFullyMatched(matched) {
		t.Error("want fully matched")
	}
	noArtist := matched
	noArtist.Artist = nil
	if IsFullyMatched(noArtist) {
		t.Error("want not matched (no artist)")
	}
	noTracks := matched
	noTracks.Tracks = nil
	if IsFullyMatched(noTracks) {
		t.Error("want not matched (no tracks)")
	}
	withRejection := matched
	withRejection.Rejections = []Rejection{{Reason: "no album found"}}
	if IsFullyMatched(withRejection) {
		t.Error("want not matched (has rejection)")
	}
}

func TestSkipReasonPrefersLidarrRejectionText(t *testing.T) {
	item := ImportCandidate{Rejections: []Rejection{{Reason: "Track already has file"}}}
	if got := SkipReason(item); got != "Track already has file" {
		t.Errorf("got %q", got)
	}
}

func TestSkipReasonFallsBackToWhichFieldIsMissing(t *testing.T) {
	if got := SkipReason(ImportCandidate{}); got != "no artist match" {
		t.Errorf("got %q, want 'no artist match'", got)
	}
	if got := SkipReason(ImportCandidate{Artist: &IDRef{ID: 1}}); got != "no album match" {
		t.Errorf("got %q, want 'no album match'", got)
	}
	if got := SkipReason(ImportCandidate{Artist: &IDRef{ID: 1}, Album: &IDRef{ID: 2}}); got != "no track match" {
		t.Errorf("got %q, want 'no track match'", got)
	}
}

func TestHasMissingAlbumRejection(t *testing.T) {
	item := ImportCandidate{Rejections: []Rejection{{Reason: "Couldn't find similar album for [/music/Artist/Album (2011)]"}}}
	if !HasMissingAlbumRejection(item) {
		t.Error("want true")
	}
	other := ImportCandidate{Rejections: []Rejection{{Reason: "Track already has file"}}}
	if HasMissingAlbumRejection(other) {
		t.Error("want false")
	}
}

func TestHasMissingTracksRejection(t *testing.T) {
	item := ImportCandidate{Rejections: []Rejection{{Reason: "Has missing tracks"}}}
	if !HasMissingTracksRejection(item) {
		t.Error("want true")
	}
	other := ImportCandidate{Rejections: []Rejection{{Reason: "Track already has file"}}}
	if HasMissingTracksRejection(other) {
		t.Error("want false")
	}
}

func TestHasExistingFileRejection(t *testing.T) {
	blocked := ImportCandidate{Album: &IDRef{ID: 5}, Rejections: []Rejection{{Reason: "Track already has file"}}}
	if !HasExistingFileRejection(blocked) {
		t.Error("want true")
	}
	noAlbum := ImportCandidate{Album: nil, Rejections: []Rejection{{Reason: "Track already has file"}}}
	if HasExistingFileRejection(noAlbum) {
		t.Error("want false (no album)")
	}
	unrelated := ImportCandidate{Album: &IDRef{ID: 5}, Rejections: []Rejection{{Reason: "no audio files"}}}
	if HasExistingFileRejection(unrelated) {
		t.Error("want false (unrelated rejection)")
	}
}

func TestGetArtistIDForPathMatchesExactPath(t *testing.T) {
	srv := sequencedServer(t, jsonHandler(`[{"id":434,"path":"/music/Jelly Roll"},{"id":967,"path":"/music/Jelly Roll (Rock band from Georgia)"}]`))
	id, ok, err := GetArtistIDForPath(srv.URL, "key", "/music/Jelly Roll")
	if err != nil {
		t.Fatal(err)
	}
	if !ok || id != 434 {
		t.Errorf("got (%d, %v), want (434, true)", id, ok)
	}
}

func TestGetArtistIDForPathMatchesSubfolder(t *testing.T) {
	srv := sequencedServer(t, jsonHandler(`[{"id":855,"path":"/music/Alex Anwandter"}]`))
	id, ok, err := GetArtistIDForPath(srv.URL, "key", "/music/Alex Anwandter/Amiga (2016)")
	if err != nil {
		t.Fatal(err)
	}
	if !ok || id != 855 {
		t.Errorf("got (%d, %v), want (855, true)", id, ok)
	}
}

func TestGetArtistIDForPathReturnsFalseWithoutMatch(t *testing.T) {
	srv := sequencedServer(t, jsonHandler(`[{"id":1,"path":"/music/Someone Else"}]`))
	_, ok, err := GetArtistIDForPath(srv.URL, "key", "/music/Jelly Roll")
	if err != nil {
		t.Fatal(err)
	}
	if ok {
		t.Error("want ok = false")
	}
}

func TestGetArtistIDForPathDoesNotFalseMatchSimilarlyNamedPrefix(t *testing.T) {
	srv := sequencedServer(t, jsonHandler(`[{"id":1,"path":"/music/Jelly Rollers"}]`))
	_, ok, err := GetArtistIDForPath(srv.URL, "key", "/music/Jelly Roll")
	if err != nil {
		t.Fatal(err)
	}
	if ok {
		t.Error("want ok = false")
	}
}

func TestGetManualImportCandidatesPassesFolderAndReturnsJSON(t *testing.T) {
	var gotQuery string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.RawQuery
		w.Write([]byte(`[{"path":"/music/song.mp3"}]`))
	}))
	defer srv.Close()

	candidates, err := GetManualImportCandidates(srv.URL, "key", "/music", nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(candidates) != 1 || candidates[0].Path != "/music/song.mp3" {
		t.Errorf("got %+v", candidates)
	}
	if !strings.Contains(gotQuery, "folder=%2Fmusic") {
		t.Errorf("query = %q, want folder=/music", gotQuery)
	}
	if strings.Contains(gotQuery, "artistId") {
		t.Errorf("query = %q, want no artistId", gotQuery)
	}
}

func TestGetManualImportCandidatesIncludesArtistIDWhenGiven(t *testing.T) {
	var gotQuery string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.RawQuery
		w.Write([]byte(`[]`))
	}))
	defer srv.Close()

	artistID := 855
	if _, err := GetManualImportCandidates(srv.URL, "key", "/music", &artistID); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(gotQuery, "artistId=855") {
		t.Errorf("query = %q, want artistId=855", gotQuery)
	}
}

func TestGetManualImportCandidatesRaisesClearErrorOnTimeout(t *testing.T) {
	noSleep(t)
	orig := ManualImportScanTimeout
	ManualImportScanTimeout = 20 * time.Millisecond
	t.Cleanup(func() { ManualImportScanTimeout = orig })

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(200 * time.Millisecond)
		w.Write([]byte(`[]`))
	}))
	defer srv.Close()

	_, err := GetManualImportCandidates(srv.URL, "key", "/music", nil)
	if err == nil || !contains(err.Error(), "timed out") {
		t.Errorf("err = %v, want 'timed out'", err)
	}
}

func TestSubmitManualImportPostsCommandAndReturnsID(t *testing.T) {
	var payload map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&payload)
		w.Write([]byte(`{"id":42}`))
	}))
	defer srv.Close()

	candidate := ImportCandidate{
		Path: "/music/a.mp3", Artist: &IDRef{ID: 1}, Album: &IDRef{ID: 2}, AlbumReleaseID: 99, Tracks: []IDRef{{ID: 3}},
	}
	id, err := SubmitManualImport(srv.URL, "key", []ImportCandidate{candidate}, "auto")
	if err != nil {
		t.Fatal(err)
	}
	if id != 42 {
		t.Errorf("id = %d, want 42", id)
	}
	if payload["name"] != "ManualImport" || payload["importMode"] != "auto" || payload["replaceExistingFiles"] != false {
		t.Errorf("payload = %+v", payload)
	}
}

func TestSubmitManualImportFlattensNestedIDsForTheCommand(t *testing.T) {
	var payload map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&payload)
		w.Write([]byte(`{"id":42}`))
	}))
	defer srv.Close()

	candidate := ImportCandidate{
		Path: "/music/a.mp3", Artist: &IDRef{ID: 855}, Album: &IDRef{ID: 10098}, AlbumReleaseID: 99,
		Tracks: []IDRef{{ID: 811503}}, Quality: json.RawMessage(`{"quality":{"id":4,"name":"MP3-320"}}`),
	}
	if _, err := SubmitManualImport(srv.URL, "key", []ImportCandidate{candidate}, "auto"); err != nil {
		t.Fatal(err)
	}
	files := payload["files"].([]any)
	if len(files) != 1 {
		t.Fatalf("files = %+v", files)
	}
	f := files[0].(map[string]any)
	if f["artistId"] != float64(855) || f["albumId"] != float64(10098) || f["albumReleaseId"] != float64(99) {
		t.Errorf("f = %+v", f)
	}
	trackIDs := f["trackIds"].([]any)
	if len(trackIDs) != 1 || trackIDs[0] != float64(811503) {
		t.Errorf("trackIds = %+v", trackIDs)
	}
}

func TestGetAlbumTrackfilesPassesAlbumID(t *testing.T) {
	var gotQuery string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.RawQuery
		w.Write([]byte(`[{"id":1},{"id":2}]`))
	}))
	defer srv.Close()

	trackfiles, err := GetAlbumTrackfiles(srv.URL, "key", 5)
	if err != nil {
		t.Fatal(err)
	}
	if len(trackfiles) != 2 {
		t.Errorf("got %+v", trackfiles)
	}
	if gotQuery != "albumId=5" {
		t.Errorf("query = %q, want albumId=5", gotQuery)
	}
}

func withFrozenClock(t *testing.T, times ...time.Time) {
	t.Helper()
	i := 0
	orig := nowFunc
	nowFunc = func() time.Time {
		if i >= len(times) {
			return times[len(times)-1]
		}
		v := times[i]
		i++
		return v
	}
	t.Cleanup(func() { nowFunc = orig })
}

func TestWaitForCommandReturnsOnceCompleted(t *testing.T) {
	noSleep(t)
	srv := sequencedServer(t, jsonHandler(`{"status":"started"}`), jsonHandler(`{"status":"completed"}`))

	result, err := WaitForCommand(srv.URL, "key", 42, CommandPollTimeout, CommandQueueTimeout, nil)
	if err != nil {
		t.Fatal(err)
	}
	if result.Status != "completed" {
		t.Errorf("status = %q", result.Status)
	}
}

func TestWaitForCommandReportsProgressOnStatusChangeOnly(t *testing.T) {
	noSleep(t)
	srv := sequencedServer(t, jsonHandler(`{"status":"queued"}`), jsonHandler(`{"status":"queued"}`), jsonHandler(`{"status":"completed"}`))

	var messages []string
	if _, err := WaitForCommand(srv.URL, "key", 42, CommandPollTimeout, CommandQueueTimeout, func(m string) { messages = append(messages, m) }); err != nil {
		t.Fatal(err)
	}
	want := []string{"Lidarr command 42: queued", "Lidarr command 42: completed"}
	if len(messages) != len(want) {
		t.Fatalf("messages = %v, want %v", messages, want)
	}
	for i := range want {
		if messages[i] != want[i] {
			t.Errorf("messages[%d] = %q, want %q", i, messages[i], want[i])
		}
	}
}

func TestWaitForCommandTimesOut(t *testing.T) {
	noSleep(t)
	base := time.Now()
	withFrozenClock(t, base, base, base.Add(100*time.Second))
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"status":"started"}`))
	}))
	defer srv.Close()

	_, err := WaitForCommand(srv.URL, "key", 42, 1*time.Second, CommandQueueTimeout, nil)
	if err == nil || !contains(err.Error(), "Timed out") {
		t.Errorf("err = %v, want Timed out", err)
	}
}

func TestWaitForCommandDoesNotTimeOutWhileMerelyQueued(t *testing.T) {
	noSleep(t)
	base := time.Now()
	withFrozenClock(t, base, base, base.Add(500*time.Second))
	srv := sequencedServer(t, jsonHandler(`{"status":"queued"}`), jsonHandler(`{"status":"queued"}`), jsonHandler(`{"status":"completed"}`))

	result, err := WaitForCommand(srv.URL, "key", 42, 1*time.Second, 10_000*time.Second, nil)
	if err != nil {
		t.Fatal(err)
	}
	if result.Status != "completed" {
		t.Errorf("status = %q", result.Status)
	}
}

func TestWaitForCommandTimesOutWhileQueuedTooLong(t *testing.T) {
	noSleep(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"status":"queued"}`))
	}))
	defer srv.Close()

	_, err := WaitForCommand(srv.URL, "key", 42, CommandPollTimeout, 10*time.Millisecond, nil)
	if err == nil || !contains(err.Error(), "still queued") {
		t.Errorf("err = %v, want 'still queued'", err)
	}
}

func TestClearStaleTrackfilesOnlyDeletesRecordsWhoseFileIsActuallyGone(t *testing.T) {
	dir := t.TempDir()
	stillThere := filepath.Join(dir, "02 - still there.mp3")
	os.WriteFile(stillThere, []byte("data"), 0o644)

	var deletedPaths []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodDelete {
			deletedPaths = append(deletedPaths, r.URL.Path)
			return
		}
		fmt.Fprintf(w, `[{"id":1,"path":%q},{"id":2,"path":%q}]`, filepath.Join(dir, "01 - gone.flac"), stillThere)
	}))
	defer srv.Close()

	count, err := ClearStaleTrackfiles(srv.URL, "key", 5, "", "", nil)
	if err != nil {
		t.Fatal(err)
	}
	if count != 1 {
		t.Errorf("count = %d, want 1", count)
	}
	if len(deletedPaths) != 1 || !strings.HasSuffix(deletedPaths[0], "/1") {
		t.Errorf("deletedPaths = %v, want just id 1", deletedPaths)
	}
	if _, err := os.Stat(stillThere); err != nil {
		t.Error("still-there file must not be touched")
	}
}

func TestClearStaleTrackfilesPacesOutMultipleDeletes(t *testing.T) {
	noSleep(t)
	sleeps := 0
	orig := sleepFunc
	sleepFunc = func(d time.Duration) { sleeps++ }
	t.Cleanup(func() { sleepFunc = orig })

	dir := t.TempDir()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodDelete {
			return
		}
		fmt.Fprintf(w, `[{"id":1,"path":%q},{"id":2,"path":%q},{"id":3,"path":%q}]`,
			filepath.Join(dir, "01.flac"), filepath.Join(dir, "02.flac"), filepath.Join(dir, "03.flac"))
	}))
	defer srv.Close()

	count, err := ClearStaleTrackfiles(srv.URL, "key", 5, "", "", nil)
	if err != nil {
		t.Fatal(err)
	}
	if count != 3 {
		t.Errorf("count = %d, want 3", count)
	}
	if sleeps != 2 {
		t.Errorf("sleeps = %d, want 2 (pause between each of 3 deletes, not before the first)", sleeps)
	}
}

func TestClearStaleTrackfilesSkipsWhenParentDirectoryIsUnreachable(t *testing.T) {
	deleteCalled := false
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodDelete {
			deleteCalled = true
			return
		}
		w.Write([]byte(`[{"id":1,"path":"/totally/unrelated/namespace/song.mp3"}]`))
	}))
	defer srv.Close()

	count, err := ClearStaleTrackfiles(srv.URL, "key", 5, "", "", nil)
	if err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Errorf("count = %d, want 0", count)
	}
	if deleteCalled {
		t.Error("delete must not be called")
	}
}

func TestClearStaleTrackfilesUsesPathMappingWhenConfigured(t *testing.T) {
	dir := t.TempDir()
	os.MkdirAll(filepath.Join(dir, "Artist"), 0o755)
	os.WriteFile(filepath.Join(dir, "Artist", "song.mp3"), []byte("data"), 0o644)

	deleteCalled := false
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodDelete {
			deleteCalled = true
			return
		}
		w.Write([]byte(`[{"id":1,"path":"/music/Artist/song.mp3"}]`))
	}))
	defer srv.Close()

	count, err := ClearStaleTrackfiles(srv.URL, "key", 5, dir, "/music", nil)
	if err != nil {
		t.Fatal(err)
	}
	if count != 0 || deleteCalled {
		t.Error("file exists under the mapped path, must not be deleted")
	}
}

func TestImportFolderResolvesAndPassesArtistID(t *testing.T) {
	var gotQuery string
	srv := sequencedServer(t,
		jsonHandler(`[{"id":434,"path":"/music/Jelly Roll"}]`),
		jsonHandler(`[]`),
		func(w http.ResponseWriter, r *http.Request) { gotQuery = r.URL.RawQuery; w.Write([]byte(`[]`)) },
	)

	if _, _, _, err := ImportFolder(srv.URL, "key", "/music/Jelly Roll", "auto", "", "", nil, nil); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(gotQuery, "artistId=434") {
		t.Errorf("query = %q, want artistId=434", gotQuery)
	}
}

func TestImportFolderClearsArtistStaleTrackfilesBeforeScanning(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "still-here.mp3"), []byte("data"), 0o644)

	var deletedPaths []string
	srv := sequencedServer(t,
		jsonHandler(`[{"id":434,"path":"/music/Jelly Roll"}]`),
		jsonHandler(fmt.Sprintf(`[{"id":1,"path":"/music/Jelly Roll/gone.flac"},{"id":2,"path":"/music/Jelly Roll/still-here.mp3"}]`)),
		jsonHandler(`[]`),
	)
	// deletes go to the same server but sequencedServer only handles GETs
	// distinctly from the sequence; add a separate delete-tracking wrapper.
	origHandler := srv.Config.Handler
	srv.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodDelete {
			deletedPaths = append(deletedPaths, r.URL.Path)
			return
		}
		origHandler.ServeHTTP(w, r)
	})

	if _, _, _, err := ImportFolder(srv.URL, "key", "/music/Jelly Roll", "auto", dir, "/music/Jelly Roll", nil, nil); err != nil {
		t.Fatal(err)
	}
	if len(deletedPaths) != 1 || !strings.HasSuffix(deletedPaths[0], "/1") {
		t.Errorf("deletedPaths = %v, want just id 1", deletedPaths)
	}
}

func TestImportFolderReportsProgressAsItHappens(t *testing.T) {
	srv := sequencedServer(t, noArtistMatchHandler(), jsonHandler(`[]`))

	var messages []string
	if _, _, _, err := ImportFolder(srv.URL, "key", "/music/Unmatched Artist", "auto", "", "", func(m string) { messages = append(messages, m) }, nil); err != nil {
		t.Fatal(err)
	}
	found := map[string]bool{}
	for _, m := range messages {
		if strings.Contains(m, "Resolving artist") {
			found["resolve"] = true
		}
		if strings.Contains(m, "scan the folder") {
			found["scan"] = true
		}
		if strings.HasPrefix(m, "Done:") {
			found["done"] = true
		}
	}
	if !found["resolve"] || !found["scan"] || !found["done"] {
		t.Errorf("messages = %v, missing expected steps", messages)
	}
}

func TestImportFolderRemapsFolderToLidarrsPathBeforeScanning(t *testing.T) {
	var gotQuery string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.RawQuery
		w.Write([]byte(`[]`))
	}))
	defer srv.Close()

	if _, _, _, err := ImportFolder(srv.URL, "key", "/home/user/Music/Artist", "auto", "/home/user/Music", "/music", nil, nil); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(gotQuery, "folder=%2Fmusic%2FArtist") {
		t.Errorf("query = %q, want folder=/music/Artist", gotQuery)
	}
}

func TestImportFolderSubmitsLargeBatchesInChunks(t *testing.T) {
	noSleep(t)
	var candidatesJSON strings.Builder
	candidatesJSON.WriteByte('[')
	for i := 0; i < 45; i++ {
		if i > 0 {
			candidatesJSON.WriteByte(',')
		}
		candidatesJSON.WriteString(matchedCandidateJSON(fmt.Sprintf("/music/%03d.mp3", i), 1, 2, i))
	}
	candidatesJSON.WriteByte(']')

	var batchSizes []int
	postCount := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost:
			var payload struct {
				Files []json.RawMessage `json:"files"`
			}
			json.NewDecoder(r.Body).Decode(&payload)
			batchSizes = append(batchSizes, len(payload.Files))
			postCount++
			fmt.Fprintf(w, `{"id":%d}`, postCount)
		case strings.Contains(r.URL.Path, "/command/"):
			w.Write([]byte(`{"status":"completed"}`))
		case strings.Contains(r.URL.Path, "/artist"):
			w.Write([]byte(`[]`))
		default:
			w.Write([]byte(candidatesJSON.String()))
		}
	}))
	defer srv.Close()

	imported, skipped, _, err := ImportFolder(srv.URL, "key", "/music", "auto", "", "", nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if imported != 45 || skipped != 0 {
		t.Errorf("imported=%d skipped=%d, want 45/0", imported, skipped)
	}
	if len(batchSizes) != 3 || batchSizes[0] != 20 || batchSizes[1] != 20 || batchSizes[2] != 5 {
		t.Errorf("batchSizes = %v, want [20 20 5]", batchSizes)
	}
}

func TestImportFolderOnlySubmitsMatchedCandidates(t *testing.T) {
	candidatesJSON := `[` + matchedCandidateJSON("/music/a.mp3", 1, 2, 3) +
		`,{"path":"/music/b.mp3","artist":null,"album":null,"tracks":[],"rejections":[{"reason":"no match"}]}]`

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost:
			w.Write([]byte(`{"id":2}`))
		case strings.Contains(r.URL.Path, "/command/"):
			w.Write([]byte(`{"status":"completed"}`))
		case strings.Contains(r.URL.Path, "/artist"):
			w.Write([]byte(`[]`))
		default:
			w.Write([]byte(candidatesJSON))
		}
	}))
	defer srv.Close()

	imported, skipped, skippedNames, err := ImportFolder(srv.URL, "key", "/music", "auto", "", "", nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if imported != 1 || skipped != 1 {
		t.Errorf("imported=%d skipped=%d, want 1/1", imported, skipped)
	}
	if len(skippedNames) != 1 || skippedNames[0] != "b.mp3: no match" {
		t.Errorf("skippedNames = %v", skippedNames)
	}
}

func TestImportFolderHintsAtReleaseMismatchForMissingTracksSkips(t *testing.T) {
	candidates := `[{"path":"/music/Absurd Minds/The Focus/01.mp3","artist":{"id":855},"album":{"id":12},"tracks":[],"rejections":[{"reason":"Has missing tracks"}]}]`

	srv := sequencedServer(t,
		jsonHandler(`[{"id":855,"path":"/music/Absurd Minds"}]`),
		jsonHandler(`[]`),
		jsonHandler(candidates),
	)

	imported, skipped, skippedNames, err := ImportFolder(srv.URL, "key", "/music/Absurd Minds/The Focus", "auto", "", "", nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if imported != 0 || skipped != 1 {
		t.Errorf("imported=%d skipped=%d, want 0/1", imported, skipped)
	}
	if len(skippedNames) != 1 || !strings.HasPrefix(skippedNames[0], "01.mp3: Has missing tracks (Lidarr may have matched the wrong release") {
		t.Errorf("skippedNames = %v", skippedNames)
	}
}

func TestImportFolderSkipsSubmitWhenNothingMatched(t *testing.T) {
	postCalled := false
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost {
			postCalled = true
			return
		}
		w.Write([]byte(`[{"path":"/music/b.mp3","artist":null,"album":null,"tracks":[],"rejections":["no match"]}]`))
	}))
	defer srv.Close()

	imported, skipped, skippedNames, err := ImportFolder(srv.URL, "key", "/music", "auto", "", "", nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if imported != 0 || skipped != 1 || postCalled {
		t.Errorf("imported=%d skipped=%d postCalled=%v", imported, skipped, postCalled)
	}
	if len(skippedNames) != 1 || skippedNames[0] != "b.mp3: no match" {
		t.Errorf("skippedNames = %v", skippedNames)
	}
}

func TestImportFolderClearsStaleTrackfileAndRetries(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "a.mp3"), []byte("data"), 0o644)

	firstScan := `[{"path":"/music/a.mp3","artist":{"id":1},"album":{"id":5},"tracks":[],"rejections":[{"reason":"Track already has file"}]}]`
	secondScan := `[` + matchedCandidateJSON("/music/a.mp3", 1, 5, 3) + `]`

	deleteCalled := 0
	getCount := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodDelete:
			deleteCalled++
		case r.Method == http.MethodPost:
			w.Write([]byte(`{"id":2}`))
		case strings.Contains(r.URL.Path, "/command/"):
			w.Write([]byte(`{"status":"completed"}`))
		case strings.Contains(r.URL.Path, "/artist") && !strings.Contains(r.URL.Path, "trackfile"):
			w.Write([]byte(`[]`))
		case strings.Contains(r.URL.Path, "/trackfile"):
			w.Write([]byte(`[{"id":99,"path":"/music/a.flac"}]`))
		default:
			getCount++
			if getCount == 1 {
				w.Write([]byte(firstScan))
			} else {
				w.Write([]byte(secondScan))
			}
		}
	}))
	defer srv.Close()

	imported, skipped, skippedNames, err := ImportFolder(srv.URL, "key", "/music", "auto", dir, "/music", nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if imported != 1 || skipped != 0 || len(skippedNames) != 0 {
		t.Errorf("imported=%d skipped=%d skippedNames=%v", imported, skipped, skippedNames)
	}
	if deleteCalled != 1 {
		t.Errorf("deleteCalled = %d, want 1", deleteCalled)
	}
}

func TestImportFolderRaisesOnLidarrReportedFailure(t *testing.T) {
	candidates := `[` + matchedCandidateJSON("/music/a.mp3", 1, 2, 3) + `]`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost:
			w.Write([]byte(`{"id":2}`))
		case strings.Contains(r.URL.Path, "/command/"):
			w.Write([]byte(`{"status":"failed","message":"disk full"}`))
		case strings.Contains(r.URL.Path, "/artist"):
			w.Write([]byte(`[]`))
		default:
			w.Write([]byte(candidates))
		}
	}))
	defer srv.Close()

	_, _, _, err := ImportFolder(srv.URL, "key", "/music", "auto", "", "", nil, nil)
	if err == nil || !contains(err.Error(), "disk full") {
		t.Errorf("err = %v, want 'disk full'", err)
	}
}

func TestPlanForceReimportRaisesWhenNoArtistMatchesTheFolder(t *testing.T) {
	srv := sequencedServer(t, noArtistMatchHandler())
	_, err := PlanForceReimport(srv.URL, "key", "/music/Unknown Artist", "", "", nil)
	if err == nil || !contains(err.Error(), "No Lidarr artist found") {
		t.Errorf("err = %v, want 'No Lidarr artist found'", err)
	}
}

func TestPlanForceReimportIsReadOnly(t *testing.T) {
	dir := t.TempDir()
	folder := filepath.Join(dir, "Jelly Roll")
	os.MkdirAll(folder, 0o755)
	os.WriteFile(filepath.Join(folder, "song.mp3"), []byte("data"), 0o644)

	deleteCalled, postCalled := false, false
	srv := sequencedServer(t,
		jsonHandler(`[{"id":434,"path":"/music/Jelly Roll"}]`),
		jsonHandler(`[{"id":1,"path":"/music/Jelly Roll/song.mp3"},{"id":2,"path":"/music/Jelly Roll/gone.mp3"}]`),
	)
	origHandler := srv.Config.Handler
	srv.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodDelete {
			deleteCalled = true
			return
		}
		if r.Method == http.MethodPost {
			postCalled = true
			return
		}
		origHandler.ServeHTTP(w, r)
	})

	inScope, err := PlanForceReimport(srv.URL, "key", folder, dir, "/music", nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(inScope) != 1 || inScope[0].Trackfile.ID != 1 {
		t.Errorf("inScope = %+v, want just id 1", inScope)
	}
	if _, err := os.Stat(filepath.Join(folder, "song.mp3")); err != nil {
		t.Error("song.mp3 must still exist")
	}
	if deleteCalled || postCalled {
		t.Error("must not call delete or post")
	}
}

func TestForceReimportFolderRaisesWhenNoArtistMatchesTheFolder(t *testing.T) {
	srv := sequencedServer(t, noArtistMatchHandler())
	_, _, _, err := ForceReimportFolder(srv.URL, "key", "/music/Unknown Artist", "auto", "", "", nil, nil)
	if err == nil || !contains(err.Error(), "No Lidarr artist found") {
		t.Errorf("err = %v, want 'No Lidarr artist found'", err)
	}
}

func TestForceReimportFolderMovesFileAsideDeletesRecordThenMovesItBack(t *testing.T) {
	dir := t.TempDir()
	folder := filepath.Join(dir, "Jelly Roll")
	os.MkdirAll(folder, 0o755)
	songPath := filepath.Join(folder, "song.mp3")
	os.WriteFile(songPath, []byte("data"), 0o644)

	var deletedPaths []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodDelete:
			deletedPaths = append(deletedPaths, r.URL.Path)
		case strings.Contains(r.URL.Path, "/artist") && !strings.Contains(r.URL.Path, "trackfile"):
			w.Write([]byte(`[{"id":434,"path":"/music/Jelly Roll"}]`))
		case strings.Contains(r.URL.Path, "/trackfile"):
			// First call (from PlanForceReimport) sees the file; the
			// re-scan inside ImportFolder afterwards sees none left.
			if !fileExists(songPath) {
				w.Write([]byte(`[]`))
				return
			}
			w.Write([]byte(`[{"id":1,"path":"/music/Jelly Roll/song.mp3"}]`))
		default:
			w.Write([]byte(`[]`))
		}
	}))
	defer srv.Close()

	imported, skipped, _, err := ForceReimportFolder(srv.URL, "key", folder, "auto", dir, "/music", nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(deletedPaths) != 1 || !strings.HasSuffix(deletedPaths[0], "/1") {
		t.Errorf("deletedPaths = %v, want just id 1", deletedPaths)
	}
	if _, err := os.Stat(songPath); err != nil {
		t.Error("song.mp3 must be moved back, not deleted")
	}
	if imported != 0 || skipped != 0 {
		t.Errorf("imported=%d skipped=%d", imported, skipped)
	}
	matches, _ := filepath.Glob(filepath.Join(dir, ".flac2mp3-reimport-*"))
	if len(matches) != 0 {
		t.Errorf("holding dir left behind: %v", matches)
	}
}

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

func TestForceReimportFolderMovesFileBackEvenIfDeletingItsRecordFails(t *testing.T) {
	noSleep(t)
	dir := t.TempDir()
	folder := filepath.Join(dir, "Jelly Roll")
	os.MkdirAll(folder, 0o755)
	songPath := filepath.Join(folder, "song.mp3")
	os.WriteFile(songPath, []byte("data"), 0o644)

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodDelete:
			w.WriteHeader(http.StatusInternalServerError)
		case strings.Contains(r.URL.Path, "/artist"):
			w.Write([]byte(`[{"id":434,"path":"/music/Jelly Roll"}]`))
		case strings.Contains(r.URL.Path, "/trackfile"):
			w.Write([]byte(`[{"id":1,"path":"/music/Jelly Roll/song.mp3"}]`))
		default:
			w.Write([]byte(`[]`))
		}
	}))
	defer srv.Close()

	_, _, _, err := ForceReimportFolder(srv.URL, "key", folder, "auto", dir, "/music", nil, nil)
	if err == nil {
		t.Fatal("expected an error from the failed delete")
	}
	if _, statErr := os.Stat(songPath); statErr != nil {
		t.Error("song.mp3 must be restored despite the delete failure")
	}
	matches, _ := filepath.Glob(filepath.Join(dir, ".flac2mp3-reimport-*"))
	if len(matches) != 0 {
		t.Errorf("holding dir left behind: %v", matches)
	}
}

func TestForceReimportFolderIgnoresTrackfilesOutsideTheFolder(t *testing.T) {
	dir := t.TempDir()
	folder := filepath.Join(dir, "Jelly Roll", "Album One")
	os.MkdirAll(folder, 0o755)
	os.WriteFile(filepath.Join(folder, "song.mp3"), []byte("data"), 0o644)
	otherAlbum := filepath.Join(dir, "Jelly Roll", "Album Two")
	os.MkdirAll(otherAlbum, 0o755)
	otherFile := filepath.Join(otherAlbum, "other.mp3")
	os.WriteFile(otherFile, []byte("data"), 0o644)

	var deletedPaths []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodDelete:
			deletedPaths = append(deletedPaths, r.URL.Path)
		case strings.Contains(r.URL.Path, "/artist") && !strings.Contains(r.URL.Path, "trackfile"):
			w.Write([]byte(`[{"id":434,"path":"/music/Jelly Roll"}]`))
		case strings.Contains(r.URL.Path, "/trackfile"):
			w.Write([]byte(`[{"id":1,"path":"/music/Jelly Roll/Album One/song.mp3"},{"id":2,"path":"/music/Jelly Roll/Album Two/other.mp3"}]`))
		default:
			w.Write([]byte(`[]`))
		}
	}))
	defer srv.Close()

	if _, _, _, err := ForceReimportFolder(srv.URL, "key", folder, "auto", dir, "/music", nil, nil); err != nil {
		t.Fatal(err)
	}
	if len(deletedPaths) != 1 || !strings.HasSuffix(deletedPaths[0], "/1") {
		t.Errorf("deletedPaths = %v, want just id 1", deletedPaths)
	}
	if _, err := os.Stat(otherFile); err != nil {
		t.Error("other album's file must never be touched")
	}
}

func TestForceReimportFolderSkipsMoveWhenNothingInScope(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.Contains(r.URL.Path, "/artist") && !strings.Contains(r.URL.Path, "trackfile"):
			w.Write([]byte(`[{"id":434,"path":"/music/Jelly Roll"}]`))
		default:
			w.Write([]byte(`[]`))
		}
	}))
	defer srv.Close()

	imported, skipped, _, err := ForceReimportFolder(srv.URL, "key", "/music/Jelly Roll", "auto", "", "", nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if imported != 0 || skipped != 0 {
		t.Errorf("imported=%d skipped=%d", imported, skipped)
	}
}
