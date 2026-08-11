// This file is the Go port of lidarr.py's Manual Import workflow:
// get_manual_import_candidates/submit_manual_import/import_folder/
// force_reimport_folder, and the stale-trackfile cleanup they depend on.
//
// Not ported: get_metadata_profile_disallowed_types/explain_missing_album,
// the best-effort diagnostic lidarr.py uses to enrich a "couldn't find
// similar album" skip reason with *why* Lidarr never synced that album
// (a real MusicBrainz release excluded by the artist's metadata profile).
// import_folder still works without it - a skipped file just keeps
// Lidarr's own rejection text instead of the friendlier explanation.
package lidarr

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// ManualImportScanTimeout is a var, not a const, so tests can shrink it
// to actually trigger a client-timeout error without waiting 180s for
// one - production code never changes it.
var ManualImportScanTimeout = 180 * time.Second

const (
	CommandPollInterval  = 1 * time.Second
	CommandPollTimeout   = 300 * time.Second
	CommandQueueTimeout  = 1800 * time.Second
	ImportBatchSize      = 20
	ImportBatchPause     = 2 * time.Second
	TrackfileDeletePause = 500 * time.Millisecond
)

// nowFunc is overridden in tests so WaitForCommand's timeout logic can be
// exercised without a real clock.
var nowFunc = time.Now

// OnProgress, if non-nil, receives a short human-readable message at each
// step of a long-running operation (ImportFolder, ForceReimportFolder,
// ClearStaleTrackfiles, WaitForCommand).
type OnProgress func(message string)

func report(onProgress OnProgress, message string) {
	if onProgress != nil {
		onProgress(message)
	}
}

// IDRef is the {"id": N} shape Lidarr nests an artist/album/track as in a
// manual-import candidate.
type IDRef struct {
	ID int `json:"id"`
}

// Rejection is one entry of a candidate's "rejections" list. Lidarr
// itself always sends {"reason": "..."}; the plain-string form is
// accepted too (some test fixtures and older API versions use it),
// mirroring lidarr.py's isinstance(dict) check.
type Rejection struct {
	Reason string
}

func (r *Rejection) UnmarshalJSON(data []byte) error {
	var s string
	if err := json.Unmarshal(data, &s); err == nil {
		r.Reason = s
		return nil
	}
	var obj struct {
		Reason string `json:"reason"`
	}
	if err := json.Unmarshal(data, &obj); err != nil {
		return err
	}
	r.Reason = obj.Reason
	return nil
}

// ImportCandidate is one entry of GET /api/v1/manualimport's response -
// Lidarr's proposed match (or rejection) for one file in a scanned
// folder.
type ImportCandidate struct {
	Path                    string          `json:"path"`
	Artist                  *IDRef          `json:"artist"`
	Album                   *IDRef          `json:"album"`
	AlbumReleaseID          int             `json:"albumReleaseId"`
	Tracks                  []IDRef         `json:"tracks"`
	Quality                 json.RawMessage `json:"quality"`
	IndexerFlags            int             `json:"indexerFlags"`
	DisableReleaseSwitching bool            `json:"disableReleaseSwitching"`
	Rejections              []Rejection     `json:"rejections"`
}

func (c ImportCandidate) rejectionReasons() []string {
	reasons := make([]string, 0, len(c.Rejections))
	for _, r := range c.Rejections {
		reasons = append(reasons, r.Reason)
	}
	return reasons
}

// IsFullyMatched reports whether Lidarr auto-matched this candidate to an
// artist/album/track with no unresolved rejections - i.e. it's safe to
// submit for import without a human picking the match.
func IsFullyMatched(item ImportCandidate) bool {
	return item.Artist != nil && item.Album != nil && len(item.Tracks) > 0 && len(item.Rejections) == 0
}

func anyReasonContains(item ImportCandidate, substr string) bool {
	for _, r := range item.rejectionReasons() {
		if strings.Contains(strings.ToLower(r), substr) {
			return true
		}
	}
	return false
}

// HasExistingFileRejection reports whether item was rejected specifically
// because Lidarr's database already has a TrackFile for that track - the
// standard rejection you get when this tool deleted the original (e.g. a
// FLAC just converted to MP3) but Lidarr's database hasn't been told, so
// it's still pointing at a file that's gone.
func HasExistingFileRejection(item ImportCandidate) bool {
	return item.Album != nil && (anyReasonContains(item, "already has") || anyReasonContains(item, "existing"))
}

// SkipReason returns a human-readable reason item wasn't submitted -
// Lidarr's own rejection text when there is one, otherwise which field
// (artist/album/track) failed to match.
func SkipReason(item ImportCandidate) string {
	reasons := item.rejectionReasons()
	if len(reasons) > 0 {
		return strings.Join(reasons, "; ")
	}
	if item.Artist == nil {
		return "no artist match"
	}
	if item.Album == nil {
		return "no album match"
	}
	if len(item.Tracks) == 0 {
		return "no track match"
	}
	return "unmatched"
}

const missingAlbumRejection = "couldn't find similar album"

// HasMissingAlbumRejection reports whether item was rejected because
// Lidarr couldn't match it to any album it knows about for the artist.
func HasMissingAlbumRejection(item ImportCandidate) bool {
	return anyReasonContains(item, missingAlbumRejection)
}

// HasMissingTracksRejection reports whether item was rejected because
// Lidarr matched an album but thinks its tracklist isn't fully covered by
// the local files.
func HasMissingTracksRejection(item ImportCandidate) bool {
	return anyReasonContains(item, "missing tracks")
}

// GetArtistIDForPath looks up which artist (if any) Lidarr's library has
// recorded at folder or a parent of it, so a manual-import scan doesn't
// have to infer the artist from the folder name alone. ok is false (not
// an error) if no artist's path matches.
func GetArtistIDForPath(baseURL, apiKey, folder string) (id int, ok bool, err error) {
	req, err := newRequest(http.MethodGet, buildURL(baseURL, "/api/v1/artist"), apiKey)
	if err != nil {
		return 0, false, newError("Failed to look up Lidarr's artist list: %v", err)
	}
	client := &http.Client{Timeout: RequestTimeout}
	resp, err := withRetry(func() (*http.Response, error) { return client.Do(req) })
	if err != nil {
		return 0, false, newError("Failed to look up Lidarr's artist list: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return 0, false, newError("Failed to look up Lidarr's artist list: %s", resp.Status)
	}

	var artists []struct {
		ID   int    `json:"id"`
		Path string `json:"path"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&artists); err != nil {
		return 0, false, newError("Unexpected response looking up Lidarr's artist list: %v", err)
	}

	folder = strings.TrimRight(folder, "/")
	for _, a := range artists {
		artistPath := strings.TrimRight(a.Path, "/")
		if artistPath == "" {
			continue
		}
		if folder == artistPath || strings.HasPrefix(folder, artistPath+"/") {
			return a.ID, true, nil
		}
	}
	return 0, false, nil
}

// GetManualImportCandidates asks Lidarr to scan folder and propose
// matches for each audio file, the same way its Manual Import screen
// does. folder must be a path as Lidarr itself would see it (see
// RemapPathToLidarr). Pass artistID (see GetArtistIDForPath) when known,
// so Lidarr doesn't have to infer the artist from the folder name.
func GetManualImportCandidates(baseURL, apiKey, folder string, artistID *int) ([]ImportCandidate, error) {
	values := url.Values{"folder": {folder}, "filterExistingFiles": {"true"}, "replaceExistingFiles": {"false"}}
	if artistID != nil {
		values.Set("artistId", strconv.Itoa(*artistID))
	}
	req, err := newRequest(http.MethodGet, buildURL(baseURL, "/api/v1/manualimport")+"?"+values.Encode(), apiKey)
	if err != nil {
		return nil, newError("Manual import scan failed: %v", err)
	}
	client := &http.Client{Timeout: ManualImportScanTimeout}
	resp, err := withRetry(func() (*http.Response, error) { return client.Do(req) })
	if err != nil {
		if isTimeout(err) {
			return nil, newError(
				"Manual import scan of '%s' timed out after %.0fs. Lidarr may still be working on it "+
					"(large folder, or an otherwise busy instance) - try again in a bit, or scan a smaller subfolder.",
				folder, ManualImportScanTimeout.Seconds(),
			)
		}
		return nil, newError("Manual import scan failed: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return nil, newError("Manual import scan failed: %s", resp.Status)
	}

	var candidates []ImportCandidate
	if err := json.NewDecoder(resp.Body).Decode(&candidates); err != nil {
		return nil, newError("Unexpected response from the manual import scan: %v", err)
	}
	return candidates, nil
}

func isTimeout(err error) bool {
	type timeouter interface{ Timeout() bool }
	for e := err; e != nil; e = unwrap(e) {
		if t, ok := e.(timeouter); ok && t.Timeout() {
			return true
		}
	}
	return false
}

func unwrap(err error) error {
	type wrapper interface{ Unwrap() error }
	if w, ok := err.(wrapper); ok {
		return w.Unwrap()
	}
	return nil
}

type importFile struct {
	Path                    string          `json:"path"`
	ArtistID                int             `json:"artistId"`
	AlbumID                 int             `json:"albumId"`
	AlbumReleaseID          int             `json:"albumReleaseId"`
	TrackIDs                []int           `json:"trackIds"`
	Quality                 json.RawMessage `json:"quality"`
	IndexerFlags            int             `json:"indexerFlags"`
	DisableReleaseSwitching bool            `json:"disableReleaseSwitching"`
}

// toImportFile converts a raw GET /api/v1/manualimport candidate - which
// nests artist/album/tracks as full objects, for display - into the flat
// shape POST /api/v1/command's ManualImport actually expects. Forwarding
// the raw GET item as-is silently sends artistId=0/albumId=0 and the
// command never progresses past "queued".
func toImportFile(item ImportCandidate) (importFile, error) {
	if item.Artist == nil || item.Album == nil {
		return importFile{}, newError("Unexpected manual-import candidate shape: missing artist or album")
	}
	trackIDs := make([]int, len(item.Tracks))
	for i, t := range item.Tracks {
		trackIDs[i] = t.ID
	}
	return importFile{
		Path: item.Path, ArtistID: item.Artist.ID, AlbumID: item.Album.ID,
		AlbumReleaseID: item.AlbumReleaseID, TrackIDs: trackIDs, Quality: item.Quality,
		IndexerFlags: item.IndexerFlags, DisableReleaseSwitching: item.DisableReleaseSwitching,
	}, nil
}

func newJSONRequest(method, requestURL, apiKey string, body []byte) (*http.Request, error) {
	req, err := http.NewRequest(method, requestURL, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-Api-Key", apiKey)
	req.Header.Set("Content-Type", "application/json")
	return req, nil
}

func queueCommand(baseURL, apiKey string, payload map[string]any) (int, error) {
	name, _ := payload["name"].(string)
	body, err := json.Marshal(payload)
	if err != nil {
		return 0, newError("Failed to queue the '%s' command: %v", name, err)
	}
	req, err := newJSONRequest(http.MethodPost, buildURL(baseURL, "/api/v1/command"), apiKey, body)
	if err != nil {
		return 0, newError("Failed to queue the '%s' command: %v", name, err)
	}
	client := &http.Client{Timeout: RequestTimeout}
	resp, err := withRetry(func() (*http.Response, error) { return client.Do(req) })
	if err != nil {
		return 0, newError("Failed to queue the '%s' command: %v", name, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return 0, newError("Failed to queue the '%s' command: %s", name, resp.Status)
	}
	var result struct {
		ID int `json:"id"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return 0, newError("Unexpected response queuing the '%s' command: %v", name, err)
	}
	return result.ID, nil
}

// SubmitManualImport queues a ManualImport command for the given
// (already-matched) manual-import candidates, as returned by
// GetManualImportCandidates. Returns the queued command's id for
// WaitForCommand.
func SubmitManualImport(baseURL, apiKey string, items []ImportCandidate, importMode string) (int, error) {
	files := make([]importFile, len(items))
	for i, item := range items {
		f, err := toImportFile(item)
		if err != nil {
			return 0, err
		}
		files[i] = f
	}
	return queueCommand(baseURL, apiKey, map[string]any{
		"name": "ManualImport", "files": files, "importMode": importMode, "replaceExistingFiles": false,
	})
}

// Trackfile is one of Lidarr's existing TrackFile records.
type Trackfile struct {
	ID   int    `json:"id"`
	Path string `json:"path"`
}

func getTrackfiles(baseURL, apiKey, param string, id int) ([]Trackfile, error) {
	values := url.Values{param: {strconv.Itoa(id)}}
	req, err := newRequest(http.MethodGet, buildURL(baseURL, "/api/v1/trackfile")+"?"+values.Encode(), apiKey)
	if err != nil {
		return nil, newError("Failed to look up existing track files: %v", err)
	}
	client := &http.Client{Timeout: RequestTimeout}
	resp, err := withRetry(func() (*http.Response, error) { return client.Do(req) })
	if err != nil {
		return nil, newError("Failed to look up existing track files: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return nil, newError("Failed to look up existing track files: %s", resp.Status)
	}
	var trackfiles []Trackfile
	if err := json.NewDecoder(resp.Body).Decode(&trackfiles); err != nil {
		return nil, newError("Unexpected response looking up existing track files: %v", err)
	}
	return trackfiles, nil
}

// GetAlbumTrackfiles lists Lidarr's existing TrackFile records for an
// album.
func GetAlbumTrackfiles(baseURL, apiKey string, albumID int) ([]Trackfile, error) {
	return getTrackfiles(baseURL, apiKey, "albumId", albumID)
}

// GetArtistTrackfiles lists Lidarr's existing TrackFile records for an
// artist, across all of their albums.
func GetArtistTrackfiles(baseURL, apiKey string, artistID int) ([]Trackfile, error) {
	return getTrackfiles(baseURL, apiKey, "artistId", artistID)
}

// deleteGenuinelyStaleTrackfiles is the shared safety-checked deletion
// loop: only ever deletes a record after confirming, via a real
// filesystem check, that its file is genuinely gone - and requires the
// file's parent directory to be visible at all, since if we can't even
// see the containing folder, this machine likely isn't looking at the
// same filesystem Lidarr is. When in doubt, a record is left alone: a
// leftover stale record can always be cleared on a later,
// correctly-configured run, but a wrongly deleted file cannot be undone.
func deleteGenuinelyStaleTrackfiles(baseURL, apiKey string, trackfiles []Trackfile, localRoot, lidarrRoot string, onProgress OnProgress) (int, error) {
	deleted := 0
	for _, tf := range trackfiles {
		localPath := LidarrPathToLocal(tf.Path, localRoot, lidarrRoot)
		info, err := os.Stat(filepath.Dir(localPath))
		if err != nil || !info.IsDir() {
			continue
		}
		if _, err := os.Stat(localPath); err == nil {
			continue
		}
		if deleted > 0 {
			sleepFunc(TrackfileDeletePause)
		}
		if err := DeleteTrackfile(baseURL, apiKey, tf.ID); err != nil {
			return deleted, err
		}
		deleted++
		report(onProgress, fmt.Sprintf("Lidarr: deleted stale trackfile record for %s", filepath.Base(localPath)))
	}
	return deleted, nil
}

// ClearStaleTrackfiles deletes only the TrackFile records for albumID
// whose file is genuinely gone from disk - never a blanket "clear the
// album" sweep, since DELETE /api/v1/trackfile removes the actual file,
// not just the database row. Returns how many genuinely stale records
// were deleted.
func ClearStaleTrackfiles(baseURL, apiKey string, albumID int, localRoot, lidarrRoot string, onProgress OnProgress) (int, error) {
	trackfiles, err := GetAlbumTrackfiles(baseURL, apiKey, albumID)
	if err != nil {
		return 0, err
	}
	return deleteGenuinelyStaleTrackfiles(baseURL, apiKey, trackfiles, localRoot, lidarrRoot, onProgress)
}

// ClearStaleTrackfilesForArtist is like ClearStaleTrackfiles, but across
// every album of an artist. Meant to run proactively before scanning a
// whole-artist folder: if even one track's file is stale, Lidarr's
// manual-import scan doesn't cleanly reject it - it crashes entirely -
// so clearing stale records first avoids that crash happening at all.
func ClearStaleTrackfilesForArtist(baseURL, apiKey string, artistID int, localRoot, lidarrRoot string, onProgress OnProgress) (int, error) {
	trackfiles, err := GetArtistTrackfiles(baseURL, apiKey, artistID)
	if err != nil {
		return 0, err
	}
	return deleteGenuinelyStaleTrackfiles(baseURL, apiKey, trackfiles, localRoot, lidarrRoot, onProgress)
}

// Command is a queued Lidarr command's status payload.
type Command struct {
	Status  string `json:"status"`
	Message string `json:"message"`
}

// GetCommand is a one-shot fetch of a queued command's current status -
// unlike WaitForCommand, this doesn't block until the command finishes.
func GetCommand(baseURL, apiKey string, commandID int) (Command, error) {
	req, err := newRequest(http.MethodGet, buildURL(baseURL, fmt.Sprintf("/api/v1/command/%d", commandID)), apiKey)
	if err != nil {
		return Command{}, newError("Failed to fetch command %d: %v", commandID, err)
	}
	client := &http.Client{Timeout: RequestTimeout}
	resp, err := withRetry(func() (*http.Response, error) { return client.Do(req) })
	if err != nil {
		return Command{}, newError("Failed to fetch command %d: %v", commandID, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return Command{}, newError("Failed to fetch command %d: %s", commandID, resp.Status)
	}
	var cmd Command
	if err := json.NewDecoder(resp.Body).Decode(&cmd); err != nil {
		return Command{}, newError("Unexpected response fetching command %d: %v", commandID, err)
	}
	return cmd, nil
}

// WaitForCommand polls a queued command until Lidarr reports it
// finished, returning the final command status.
//
// Two separate budgets, since "queued" and "started" mean very different
// things: queueTimeout covers time spent merely queued (something else
// unrelated is ahead of it in Lidarr's own scheduling, so it gets a
// generous allowance), while timeout covers time spent actually running,
// counted fresh once Lidarr reports "started".
func WaitForCommand(baseURL, apiKey string, commandID int, timeout, queueTimeout time.Duration, onProgress OnProgress) (Command, error) {
	queueDeadline := nowFunc().Add(queueTimeout)
	var startedDeadline time.Time
	lastStatus := ""
	for {
		cmd, err := GetCommand(baseURL, apiKey, commandID)
		if err != nil {
			return Command{}, newError("Failed to poll the Lidarr import's status: %v", err)
		}
		if cmd.Status != lastStatus {
			report(onProgress, fmt.Sprintf("Lidarr command %d: %s", commandID, cmd.Status))
			lastStatus = cmd.Status
		}
		if cmd.Status == "completed" || cmd.Status == "failed" {
			return cmd, nil
		}
		now := nowFunc()
		if cmd.Status == "started" {
			if startedDeadline.IsZero() {
				startedDeadline = now.Add(timeout)
			}
			if now.After(startedDeadline) {
				return Command{}, newError("Timed out waiting for Lidarr to finish the import")
			}
		} else if now.After(queueDeadline) {
			return Command{}, newError(
				"Command is still queued after %.0fs - Lidarr appears to be busy with other work "+
					"(e.g. a large library rescan). It will likely still complete on its own; check back later.",
				queueTimeout.Seconds(),
			)
		}
		sleepFunc(CommandPollInterval)
	}
}

// OnCommandQueued, if non-nil, is called with each batch's queued command
// id right after Lidarr accepts it, so a caller can track its status
// independently of ImportFolder's own polling.
type OnCommandQueued func(commandID int)

// ImportFolder scans folder via Lidarr's manual-import endpoint and
// submits the candidates Lidarr fully auto-matched. For candidates
// rejected specifically because Lidarr already has a file for that track
// (the standard symptom of this tool having deleted/replaced a file
// Lidarr's database doesn't know is gone), the stale TrackFile record is
// deleted and the scan retried once before giving up on those.
//
// folder is this machine's path; pass localRoot/lidarrRoot (see
// RemapPathToLidarr) if Lidarr sees the same content under a different
// mount point. A large batch of matched files is submitted in chunks of
// ImportBatchSize rather than one huge command.
//
// Returns (imported, skipped, skippedDescriptions). Never errors for
// individual unmatched files - only for connectivity/API failures or a
// Lidarr-reported import failure (which stops further batches -
// already-imported batches stay imported).
func ImportFolder(
	baseURL, apiKey string, folder, importMode, localRoot, lidarrRoot string,
	onProgress OnProgress, onCommandQueued OnCommandQueued,
) (imported, skipped int, skippedNames []string, err error) {
	lidarrFolder := RemapPathToLidarr(folder, localRoot, lidarrRoot)
	report(onProgress, fmt.Sprintf("Resolving artist for %s...", lidarrFolder))
	artistID, hasArtist, err := GetArtistIDForPath(baseURL, apiKey, lidarrFolder)
	if err != nil {
		return 0, 0, nil, err
	}
	var artistIDPtr *int
	if hasArtist {
		artistIDPtr = &artistID
		report(onProgress, "Checking for stale trackfile records...")
		cleared, err := ClearStaleTrackfilesForArtist(baseURL, apiKey, artistID, localRoot, lidarrRoot, onProgress)
		if err != nil {
			return 0, 0, nil, err
		}
		if cleared > 0 {
			report(onProgress, fmt.Sprintf("Cleared %d stale trackfile record(s)", cleared))
		}
	}

	report(onProgress, "Asking Lidarr to scan the folder for import candidates...")
	candidates, err := GetManualImportCandidates(baseURL, apiKey, lidarrFolder, artistIDPtr)
	if err != nil {
		return 0, 0, nil, err
	}
	report(onProgress, fmt.Sprintf("Lidarr returned %d candidate file(s)", len(candidates)))

	staleAlbumIDs := map[int]bool{}
	for _, c := range candidates {
		if HasExistingFileRejection(c) {
			staleAlbumIDs[c.Album.ID] = true
		}
	}
	if len(staleAlbumIDs) > 0 {
		report(onProgress, fmt.Sprintf("%d album(s) rejected as 'already has file' - clearing stale records...", len(staleAlbumIDs)))
	}
	first := true
	for albumID := range staleAlbumIDs {
		if !first {
			sleepFunc(TrackfileDeletePause)
		}
		first = false
		if _, err := ClearStaleTrackfiles(baseURL, apiKey, albumID, localRoot, lidarrRoot, onProgress); err != nil {
			return 0, 0, nil, err
		}
	}
	if len(staleAlbumIDs) > 0 {
		report(onProgress, "Rescanning after clearing stale trackfiles...")
		candidates, err = GetManualImportCandidates(baseURL, apiKey, lidarrFolder, artistIDPtr)
		if err != nil {
			return 0, 0, nil, err
		}
	}

	var matched, skippedItems []ImportCandidate
	for _, c := range candidates {
		if IsFullyMatched(c) {
			matched = append(matched, c)
		} else {
			skippedItems = append(skippedItems, c)
		}
	}
	report(onProgress, fmt.Sprintf("%d file(s) fully matched, %d skipped", len(matched), len(skippedItems)))

	totalBatches := (len(matched) + ImportBatchSize - 1) / ImportBatchSize
	for i := 0; i < len(matched); i += ImportBatchSize {
		if i > 0 {
			sleepFunc(ImportBatchPause)
		}
		end := i + ImportBatchSize
		if end > len(matched) {
			end = len(matched)
		}
		batch := matched[i:end]
		batchNum := i/ImportBatchSize + 1
		report(onProgress, fmt.Sprintf("Submitting batch %d/%d (%d file(s)) to Lidarr...", batchNum, totalBatches, len(batch)))
		commandID, err := SubmitManualImport(baseURL, apiKey, batch, importMode)
		if err != nil {
			return imported, 0, nil, err
		}
		if onCommandQueued != nil {
			onCommandQueued(commandID)
		}
		result, err := WaitForCommand(baseURL, apiKey, commandID, CommandPollTimeout, CommandQueueTimeout, onProgress)
		if err != nil {
			return imported, 0, nil, err
		}
		if result.Status == "failed" {
			message := result.Message
			if message == "" {
				message = "unknown error"
			}
			return imported, 0, nil, newError("Lidarr reported the import failed: %s", message)
		}
		imported += len(batch)
		report(onProgress, fmt.Sprintf("Batch %d/%d imported (%d/%d total)", batchNum, totalBatches, imported, len(matched)))
	}

	for _, c := range skippedItems {
		reason := SkipReason(c)
		if HasMissingTracksRejection(c) {
			reason += " (Lidarr may have matched the wrong release edition - check that this album's release in " +
				"Lidarr actually matches what's on disk, e.g. a folder mixing original and remix tracks needs a " +
				"release edition that includes both)"
		}
		skippedNames = append(skippedNames, fmt.Sprintf("%s: %s", filepath.Base(c.Path), reason))
	}

	report(onProgress, fmt.Sprintf("Done: %d imported, %d skipped", imported, len(skippedItems)))
	return imported, len(skippedItems), skippedNames, nil
}

// randomHex returns n random bytes as a hex string, used only to make a
// holding directory name collision-proof - not a security boundary.
func randomHex(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return fmt.Sprintf("%d", nowFunc().UnixNano())
	}
	return hex.EncodeToString(b)
}

// TrackfileWithPath pairs a Trackfile record with its resolved local
// path, as returned by PlanForceReimport.
type TrackfileWithPath struct {
	Trackfile Trackfile
	LocalPath string
}

// PlanForceReimport is the read-only dry-run preview for
// ForceReimportFolder: resolves the Lidarr artist for folder and returns
// every trackfile under it that ForceReimportFolder would move aside,
// clear the record for, and reimport - without moving, deleting, or
// reimporting anything. Errors if folder doesn't resolve to a known
// Lidarr artist - without that, which TrackFile records belong to this
// folder can't be determined safely.
func PlanForceReimport(baseURL, apiKey, folder, localRoot, lidarrRoot string, onProgress OnProgress) ([]TrackfileWithPath, error) {
	lidarrFolder := RemapPathToLidarr(folder, localRoot, lidarrRoot)
	report(onProgress, fmt.Sprintf("Resolving artist for %s...", lidarrFolder))
	artistID, hasArtist, err := GetArtistIDForPath(baseURL, apiKey, lidarrFolder)
	if err != nil {
		return nil, err
	}
	if !hasArtist {
		return nil, newError("No Lidarr artist found at '%s' - can't safely determine which track file records belong to this folder.", lidarrFolder)
	}

	report(onProgress, "Looking up existing track file records...")
	trackfiles, err := GetArtistTrackfiles(baseURL, apiKey, artistID)
	if err != nil {
		return nil, err
	}
	var inScope []TrackfileWithPath
	folderClean := strings.TrimRight(folder, "/")
	for _, tf := range trackfiles {
		localPath := LidarrPathToLocal(tf.Path, localRoot, lidarrRoot)
		if localPath != folderClean && !strings.HasPrefix(localPath, folderClean+"/") {
			continue
		}
		if _, err := os.Stat(localPath); err != nil {
			continue
		}
		inScope = append(inScope, TrackfileWithPath{Trackfile: tf, LocalPath: localPath})
	}
	report(onProgress, fmt.Sprintf("%d track file record(s) under this folder", len(inScope)))
	return inScope, nil
}

// ForceReimportFolder forces Lidarr to fully reimport folder, including
// files it already has a TrackFile record for - unlike ImportFolder,
// which always skips those. Useful after correcting tags on files Lidarr
// already imported with a wrong or stale match. See PlanForceReimport
// for a read-only preview of what this will do.
//
// Lidarr's only way to remove a TrackFile record - DELETE
// /api/v1/trackfile - always deletes the underlying file too; there is
// no "forget the record, keep the file" API call. To force a reimport
// without ever actually deleting anything, every already-tracked file
// under folder is moved aside to a temporary holding directory first, so
// Lidarr's existing existence-checked stale-trackfile cleanup sees each
// one as genuinely gone and safely drops just its database record; every
// file is then moved straight back to its original path before the
// normal import scan runs. Even if this is interrupted partway, no file
// is ever deleted - at worst one is left in the holding directory
// (named in the returned error) instead of back in place.
func ForceReimportFolder(
	baseURL, apiKey, folder, importMode, localRoot, lidarrRoot string,
	onProgress OnProgress, onCommandQueued OnCommandQueued,
) (imported, skipped int, skippedNames []string, err error) {
	inScope, err := PlanForceReimport(baseURL, apiKey, folder, localRoot, lidarrRoot, onProgress)
	if err != nil {
		return 0, 0, nil, err
	}

	if len(inScope) == 0 {
		report(onProgress, "No existing Lidarr track file records under this folder - nothing to clear")
		return ImportFolder(baseURL, apiKey, folder, importMode, localRoot, lidarrRoot, onProgress, onCommandQueued)
	}

	report(onProgress, fmt.Sprintf("Moving %d tracked file(s) aside to clear their Lidarr records...", len(inScope)))
	holdingDir := filepath.Join(filepath.Dir(folder), ".flac2mp3-reimport-"+randomHex(4))
	if err := os.Mkdir(holdingDir, 0o755); err != nil {
		return 0, 0, nil, newError("Failed to create holding directory %s: %v", holdingDir, err)
	}

	type movedFile struct {
		trackfile             Trackfile
		originalPath, holding string
	}
	var moved []movedFile
	var moveErr error
	for _, item := range inScope {
		rel, relErr := filepath.Rel(folder, item.LocalPath)
		if relErr != nil {
			continue
		}
		holdingPath := filepath.Join(holdingDir, rel)
		if err := os.MkdirAll(filepath.Dir(holdingPath), 0o755); err != nil {
			moveErr = newError("Failed to move a file aside for reimport: %v", err)
			break
		}
		if err := os.Rename(item.LocalPath, holdingPath); err != nil {
			moveErr = newError("Failed to move a file aside for reimport: %v", err)
			break
		}
		moved = append(moved, movedFile{trackfile: item.Trackfile, originalPath: item.LocalPath, holding: holdingPath})
	}

	// Everything below runs regardless of moveErr, mirroring lidarr.py's
	// try/finally: every file that WAS moved aside must be moved back, even
	// if the move-aside loop or the trackfile deletion failed partway.
	var deleted int
	var deleteErr error
	if moveErr == nil {
		trackfilesToDelete := make([]Trackfile, len(moved))
		for i, m := range moved {
			trackfilesToDelete[i] = m.trackfile
		}
		deleted, deleteErr = deleteGenuinelyStaleTrackfiles(baseURL, apiKey, trackfilesToDelete, localRoot, lidarrRoot, onProgress)
		if deleteErr == nil {
			report(onProgress, fmt.Sprintf("Cleared %d track file record(s)", deleted))
		}
	}

	report(onProgress, "Moving file(s) back...")
	var restoreFailures []string
	for _, m := range moved {
		if _, err := os.Stat(m.holding); err != nil {
			continue // nothing left to restore
		}
		if err := os.MkdirAll(filepath.Dir(m.originalPath), 0o755); err != nil {
			restoreFailures = append(restoreFailures, fmt.Sprintf("%s -> %s: %v", m.holding, m.originalPath, err))
			continue
		}
		if err := os.Rename(m.holding, m.originalPath); err != nil {
			restoreFailures = append(restoreFailures, fmt.Sprintf("%s -> %s: %v", m.holding, m.originalPath, err))
		}
	}
	os.Remove(holdingDir) // only succeeds once empty, i.e. everything was restored

	if len(restoreFailures) > 0 {
		return 0, 0, nil, newError(
			"Failed to move some file(s) back after clearing their Lidarr records - they're still safe in "+
				"the holding directory, not deleted: %s", strings.Join(restoreFailures, "; "),
		)
	}
	if moveErr != nil {
		return 0, 0, nil, moveErr
	}
	if deleteErr != nil {
		return 0, 0, nil, deleteErr
	}

	report(onProgress, "Re-scanning and reimporting...")
	return ImportFolder(baseURL, apiKey, folder, importMode, localRoot, lidarrRoot, onProgress, onCommandQueued)
}
