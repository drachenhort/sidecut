// Package acoustid is the Go port of core.py's check_acoustid: fingerprint
// a file with fpcalc, look it up via the AcoustID web service, and compare
// the best match against the file's existing musicbrainz_trackid tag.
//
// Not yet ported: apply_release_type/apply_release_provenance/
// correct_acoustid_mismatch, which rewrite a FLAC's own Vorbis comments in
// place - internal/flactag is read-only today, and those three need a
// FLAC metadata *writer* (see the plan doc's open questions). Check is
// purely informational until that lands.
package acoustid

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
)

const (
	LookupURL      = "https://api.acoustid.org/v2/lookup"
	MusicBrainzURL = "https://musicbrainz.org/ws/2"

	RateLimitPerSecond            = 4.0
	MusicBrainzRateLimitPerSecond = 1.0
	AutocorrectMinScore           = 0.5
	UserAgent                     = "Sidecut/1.0 (+https://github.com/drachenhort/sidecut)"
)

// Check is the result of comparing a file's fingerprint against the
// AcoustID/MusicBrainz database. By itself, purely informational - never
// blocks or modifies the file. Mirrors core.py's AcoustIDCheck.
type Check struct {
	Status       string // "match" | "mismatch" | "identified" | "no_match" | "error"
	Detail       string
	RecordingID  string
	Score        float64
	HasScore     bool
	Corrected    bool
	ReleaseType  string
	Date         string
	OriginalDate string
}

// Checker holds the dependencies check_acoustid needs, injectable for
// testing: Fingerprint defaults to running fpcalc, HTTPClient/AcoustIDURL/
// MusicBrainzURL default to the real services. The rate limiters are
// meant to be shared across every Check call from a single Checker, the
// same way core.py's module-level limiters are shared across every
// check_acoustid call.
type Checker struct {
	APIKey string

	Fingerprint    func(path string) (duration int, fingerprint string, err error)
	HTTPClient     *http.Client
	AcoustIDURL    string
	MusicBrainzURL string

	acoustidLimiter    *rateLimiter
	musicbrainzLimiter *rateLimiter
}

// NewChecker returns a Checker wired to the real fpcalc binary and the
// real AcoustID/MusicBrainz services, rate-limited the same as core.py.
func NewChecker(apiKey string) *Checker {
	return &Checker{
		APIKey:             apiKey,
		Fingerprint:        fpcalcFingerprint,
		HTTPClient:         &http.Client{},
		AcoustIDURL:        LookupURL,
		MusicBrainzURL:     MusicBrainzURL,
		acoustidLimiter:    newRateLimiter(RateLimitPerSecond),
		musicbrainzLimiter: newRateLimiter(MusicBrainzRateLimitPerSecond),
	}
}

type acoustidRecordingArtist struct {
	Name string `json:"name"`
}

type acoustidRecording struct {
	ID      string                    `json:"id"`
	Title   string                    `json:"title"`
	Artists []acoustidRecordingArtist `json:"artists"`
}

type acoustidResult struct {
	ID            string              `json:"id"`
	Score         float64             `json:"score"`
	Recordings    []acoustidRecording `json:"recordings"`
	ReleaseGroups []releaseGroup      `json:"releasegroups"`
}

type acoustidResponse struct {
	Status  string           `json:"status"`
	Results []acoustidResult `json:"results"`
	Error   struct {
		Message string `json:"message"`
	} `json:"error"`
}

type musicbrainzRecordingResponse struct {
	Releases []mbRelease `json:"releases"`
}

// acoustidLookup queries AcoustID's lookup endpoint with the given meta
// mode. The API only honors one meta mode per request - combining values
// silently drops everything but the last one - so Check makes two
// separate calls for recordings vs releasegroups data. The response body
// is parsed as JSON even on a non-2xx status, since AcoustID returns a
// specific error reason in the body that a bare status-code error would
// lose. Mirrors core.py's _acoustid_lookup.
func (c *Checker) acoustidLookup(duration int, fingerprint, meta string) (acoustidResponse, error) {
	c.acoustidLimiter.wait()
	q := url.Values{
		"client":      {c.APIKey},
		"duration":    {strconv.Itoa(duration)},
		"fingerprint": {fingerprint},
		"meta":        {meta},
		"format":      {"json"},
	}
	resp, err := c.HTTPClient.Get(c.AcoustIDURL + "?" + q.Encode())
	if err != nil {
		return acoustidResponse{}, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return acoustidResponse{}, err
	}
	var parsed acoustidResponse
	if err := json.Unmarshal(body, &parsed); err != nil {
		return acoustidResponse{}, fmt.Errorf("%s", resp.Status)
	}
	return parsed, nil
}

// musicbrainzLookupRecording queries MusicBrainz directly for every
// release (and its release-group) a recording appears on - richer than
// AcoustID's own releasegroups meta, which exposes type/secondarytypes
// but no dates. Needs no API key: MusicBrainz's web service is open, just
// rate-limited. Mirrors core.py's _musicbrainz_lookup_recording.
func (c *Checker) musicbrainzLookupRecording(recordingID string) (musicbrainzRecordingResponse, error) {
	c.musicbrainzLimiter.wait()
	req, err := http.NewRequest(http.MethodGet, c.MusicBrainzURL+"/recording/"+recordingID+"?inc=releases+release-groups&fmt=json", nil)
	if err != nil {
		return musicbrainzRecordingResponse{}, err
	}
	req.Header.Set("User-Agent", UserAgent)
	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return musicbrainzRecordingResponse{}, err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return musicbrainzRecordingResponse{}, fmt.Errorf("%s", resp.Status)
	}
	var parsed musicbrainzRecordingResponse
	if err := json.NewDecoder(resp.Body).Decode(&parsed); err != nil {
		return musicbrainzRecordingResponse{}, err
	}
	return parsed, nil
}

// lookupReleaseType re-queries AcoustID with meta=releasegroups for the
// same fingerprint and picks out the release type attached to the
// specific result Check already matched (by its AcoustID result ID,
// stable across separate lookups of the same fingerprint). Never errors:
// any failure just means no release type, not a failed check. Mirrors
// core.py's _lookup_release_type.
func (c *Checker) lookupReleaseType(duration int, fingerprint, resultID, taggedAlbum string) string {
	if resultID == "" {
		return ""
	}
	data, err := c.acoustidLookup(duration, fingerprint, "releasegroups")
	if err != nil || data.Status != "ok" {
		return ""
	}
	for _, result := range data.Results {
		if result.ID == resultID {
			return pickReleaseType(result.ReleaseGroups, taggedAlbum)
		}
	}
	return ""
}

// lookupReleaseProvenance queries MusicBrainz for the release type, this
// release's date, and its release-group's original release date, keyed
// off a MusicBrainz recording ID. Never errors: any failure just means no
// provenance data. Mirrors core.py's _lookup_release_provenance.
func (c *Checker) lookupReleaseProvenance(recordingID, taggedAlbum string) (releaseType, date, originalDate string) {
	if recordingID == "" {
		return "", "", ""
	}
	data, err := c.musicbrainzLookupRecording(recordingID)
	if err != nil {
		return "", "", ""
	}
	return pickReleaseProvenance(data.Releases, taggedAlbum)
}

// Check fingerprints path with fpcalc, looks it up via AcoustID, and
// compares the best match against the file's existing musicbrainz_trackid
// tag (if any). Never panics: any failure is reported as an "error"
// result. Mirrors core.py's check_acoustid.
func (c *Checker) Check(path string) Check {
	duration, fingerprint, err := c.Fingerprint(path)
	if err != nil {
		return Check{Status: "error", Detail: fmt.Sprintf("fingerprinting failed: %v", err)}
	}

	data, err := c.acoustidLookup(duration, fingerprint, "recordings")
	if err != nil {
		return Check{Status: "error", Detail: fmt.Sprintf("AcoustID request failed: %v", err)}
	}
	if data.Status != "ok" {
		message := data.Error.Message
		if message == "" {
			message = "unknown error"
		}
		return Check{Status: "error", Detail: "AcoustID error: " + message}
	}

	results := append([]acoustidResult(nil), data.Results...)
	sort.SliceStable(results, func(i, j int) bool { return results[i].Score > results[j].Score })
	if len(results) == 0 {
		return Check{Status: "no_match", Detail: "No AcoustID match found"}
	}

	best := results[0]
	score := best.Score
	var recordingIDs []string
	for _, r := range best.Recordings {
		if r.ID != "" {
			recordingIDs = append(recordingIDs, r.ID)
		}
	}
	summary := ""
	if len(best.Recordings) > 0 {
		var artistNames []string
		for _, a := range best.Recordings[0].Artists {
			if a.Name != "" {
				artistNames = append(artistNames, a.Name)
			}
		}
		summary = strings.Trim(strings.Join(artistNames, ", ")+" - "+best.Recordings[0].Title, " -")
	}

	existing, _ := readExistingRecording(path)

	releaseType := c.lookupReleaseType(duration, fingerprint, best.ID, existing.album)

	var bestID string
	if len(recordingIDs) > 0 {
		bestID = recordingIDs[0]
	}
	provenanceID := bestID
	if existing.recordingID != "" && containsFold(recordingIDs, existing.recordingID) {
		provenanceID = existing.recordingID
	}
	mbReleaseType, mbDate, mbOriginalDate := c.lookupReleaseProvenance(provenanceID, existing.album)
	if releaseType == "" {
		releaseType = mbReleaseType
	}

	if existing.recordingID != "" {
		if containsFold(recordingIDs, existing.recordingID) {
			return Check{
				Status: "match", Detail: fmt.Sprintf("Matches tagged recording (score %.2f)", score),
				RecordingID: existing.recordingID, Score: score, HasScore: true,
				ReleaseType: releaseType, Date: mbDate, OriginalDate: mbOriginalDate,
			}
		}
		tagged := strings.Trim(existing.artist+" - "+existing.title, " -")
		if tagged == "" {
			tagged = existing.recordingID
		}
		var detail string
		if summary != "" {
			detail = fmt.Sprintf("Tagged as '%s' (MBID %s) but AcoustID says the correct match is '%s' (MBID %s, score %.2f)",
				tagged, existing.recordingID, summary, bestID, score)
		} else {
			detail = fmt.Sprintf("Tagged as '%s' (MBID %s), but AcoustID's match (score %.2f) has no linked MusicBrainz recording to compare against",
				tagged, existing.recordingID, score)
		}
		return Check{
			Status: "mismatch", Detail: detail, RecordingID: bestID, Score: score, HasScore: true,
			ReleaseType: releaseType, Date: mbDate, OriginalDate: mbOriginalDate,
		}
	}
	if summary != "" {
		return Check{
			Status: "identified", Detail: fmt.Sprintf("AcoustID suggests '%s' (MBID %s, score %.2f)", summary, bestID, score),
			RecordingID: bestID, Score: score, HasScore: true,
			ReleaseType: releaseType, Date: mbDate, OriginalDate: mbOriginalDate,
		}
	}
	return Check{
		Status: "identified", Detail: fmt.Sprintf("AcoustID match found but has no linked MusicBrainz recording (score %.2f)", score),
		Score: score, HasScore: true,
		ReleaseType: releaseType, Date: mbDate, OriginalDate: mbOriginalDate,
	}
}
