// Package lidarr is the Go port of lidarr.py's Lidarr REST client:
// connection testing, path remapping, the retry/backoff helper everything
// else builds on, and the Manual Import workflow (see manualimport.go) -
// scanning a folder for import candidates, submitting matched ones,
// stale-trackfile cleanup, and force-reimporting a folder Lidarr already
// has files for.
//
// Not ported: get_metadata_profile_disallowed_types/explain_missing_album,
// a best-effort diagnostic for *why* an album didn't sync into Lidarr's
// library in the first place - see manualimport.go's doc comment.
package lidarr

import (
	"encoding/json"
	"fmt"
	"net/http"
	"path"
	"strings"
	"time"
)

const (
	RequestTimeout = 30 * time.Second
	RetryAttempts  = 3
	RetryBackoff   = 2 * time.Second
)

var retryableStatusCodes = map[int]bool{500: true, 502: true, 503: true, 504: true}

// LidarrError is raised for any Lidarr connectivity/API/import failure -
// the one error type callers need to check for, always human-readable.
type LidarrError struct{ msg string }

func (e *LidarrError) Error() string { return e.msg }

func newError(format string, args ...any) *LidarrError {
	return &LidarrError{msg: fmt.Sprintf(format, args...)}
}

// sleepFunc is overridden in tests so retry-backoff tests don't actually
// sleep for seconds.
var sleepFunc = time.Sleep

// withRetry calls do() (expected to perform one HTTP round trip) up to
// RetryAttempts times with backoff, retrying on a transport error or a
// 5xx response. Any other response (including 4xx) is returned as-is on
// the first attempt, since those aren't transient. Mirrors lidarr.py's
// _with_retry.
func withRetry(do func() (*http.Response, error)) (*http.Response, error) {
	var lastErr error
	for attempt := 0; attempt < RetryAttempts; attempt++ {
		resp, err := do()
		if err != nil {
			lastErr = err
		} else if !retryableStatusCodes[resp.StatusCode] {
			return resp, nil
		} else {
			resp.Body.Close()
			lastErr = fmt.Errorf("%d server error", resp.StatusCode)
		}
		if attempt < RetryAttempts-1 {
			sleepFunc(RetryBackoff * time.Duration(1<<attempt))
		}
	}
	return nil, lastErr
}

func newRequest(method, url, apiKey string) (*http.Request, error) {
	req, err := http.NewRequest(method, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-Api-Key", apiKey)
	return req, nil
}

func buildURL(baseURL, urlPath string) string {
	return strings.TrimRight(baseURL, "/") + urlPath
}

// CheckConnection returns Lidarr's version string, or a *LidarrError if
// unreachable or the API key is rejected.
func CheckConnection(baseURL, apiKey string) (string, error) {
	req, err := newRequest(http.MethodGet, buildURL(baseURL, "/api/v1/system/status"), apiKey)
	if err != nil {
		return "", newError("Could not reach Lidarr at %s: %v", baseURL, err)
	}

	client := &http.Client{Timeout: RequestTimeout}
	resp, err := withRetry(func() (*http.Response, error) { return client.Do(req) })
	if err != nil {
		return "", newError("Could not reach Lidarr at %s: %v", baseURL, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusUnauthorized {
		return "", newError("Lidarr rejected the API key")
	}
	if resp.StatusCode >= 400 {
		return "", newError("Lidarr returned an error: %s", resp.Status)
	}

	var body struct {
		Version string `json:"version"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return "unknown", nil
	}
	if body.Version == "" {
		return "unknown", nil
	}
	return body.Version, nil
}

// RemapPathToLidarr rewrites folder from this machine's view of a shared
// library to Lidarr's own view (e.g. a share mounted at /home/user/Music
// here but /music inside Lidarr's container). If folder isn't under
// localRoot, or either root is blank, returns folder unchanged.
func RemapPathToLidarr(folder, localRoot, lidarrRoot string) string {
	if localRoot == "" || lidarrRoot == "" {
		return folder
	}
	rel, ok := relativeTo(folder, localRoot)
	if !ok {
		return folder
	}
	return path.Join(lidarrRoot, rel)
}

// LidarrPathToLocal is the inverse of RemapPathToLidarr: rewrites a path
// Lidarr reports back into this machine's view. No-op if either root is
// blank, or p isn't under lidarrRoot.
func LidarrPathToLocal(p, localRoot, lidarrRoot string) string {
	if localRoot == "" || lidarrRoot == "" {
		return p
	}
	rel, ok := relativeTo(p, lidarrRoot)
	if !ok {
		return p
	}
	return path.Join(localRoot, rel)
}

// relativeTo returns p's path relative to root (both treated as
// slash-separated, matching Python's PurePosixPath.relative_to - Lidarr's
// API always deals in POSIX paths regardless of this machine's OS), and
// whether p is actually under root.
func relativeTo(p, root string) (string, bool) {
	cleanRoot := strings.TrimRight(root, "/")
	cleanP := strings.TrimRight(p, "/")
	if cleanP == cleanRoot {
		return "", true
	}
	prefix := cleanRoot + "/"
	if !strings.HasPrefix(cleanP, prefix) {
		return "", false
	}
	return strings.TrimPrefix(cleanP, prefix), true
}

// DeleteTrackfile DELETEs the file on disk, not just the database record.
// A 404 is treated as already-gone, not an error (most likely Lidarr's
// own concurrent reconciliation removed it first).
func DeleteTrackfile(baseURL, apiKey string, trackfileID int) error {
	req, err := newRequest(http.MethodDelete, buildURL(baseURL, fmt.Sprintf("/api/v1/trackfile/%d", trackfileID)), apiKey)
	if err != nil {
		return newError("Failed to delete stale track file %d: %v", trackfileID, err)
	}
	client := &http.Client{Timeout: RequestTimeout}
	resp, err := withRetry(func() (*http.Response, error) { return client.Do(req) })
	if err != nil {
		return newError("Failed to delete stale track file %d: %v", trackfileID, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		return nil
	}
	if resp.StatusCode >= 400 {
		return newError("Failed to delete stale track file %d: %s", trackfileID, resp.Status)
	}
	return nil
}

// GetQueue lists Lidarr's current download queue (GET /api/v1/queue).
func GetQueue(baseURL, apiKey string) ([]map[string]any, error) {
	req, err := newRequest(http.MethodGet, buildURL(baseURL, "/api/v1/queue")+"?pageSize=200&includeAlbum=true&includeArtist=true", apiKey)
	if err != nil {
		return nil, newError("Failed to fetch the Lidarr queue: %v", err)
	}
	client := &http.Client{Timeout: RequestTimeout}
	resp, err := withRetry(func() (*http.Response, error) { return client.Do(req) })
	if err != nil {
		return nil, newError("Failed to fetch the Lidarr queue: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return nil, newError("Failed to fetch the Lidarr queue: %s", resp.Status)
	}

	var body struct {
		Records []map[string]any `json:"records"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return nil, newError("Unexpected response fetching the Lidarr queue: %v", err)
	}
	return body.Records, nil
}
