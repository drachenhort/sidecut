package acoustid

import "strings"

// releaseGroup mirrors one entry of an AcoustID result's `releasegroups`
// list (present when the lookup is made with meta="releasegroups").
type releaseGroup struct {
	Title          string   `json:"title"`
	Type           string   `json:"type"`
	SecondaryTypes []string `json:"secondarytypes"`
}

// mbReleaseGroup mirrors the "release-group" object nested in one of
// MusicBrainz's own recording->releases entries - richer than AcoustID's
// releasegroups meta, which exposes type/secondarytypes but no dates.
type mbReleaseGroup struct {
	PrimaryType      string   `json:"primary-type"`
	SecondaryTypes   []string `json:"secondary-types"`
	FirstReleaseDate string   `json:"first-release-date"`
}

// mbRelease mirrors one entry of MusicBrainz's recording->releases list.
type mbRelease struct {
	Title        string         `json:"title"`
	Date         string         `json:"date"`
	ReleaseGroup mbReleaseGroup `json:"release-group"`
}

func containsFold(values []string, target string) bool {
	for _, v := range values {
		if strings.EqualFold(v, target) {
			return true
		}
	}
	return false
}

// pickReleaseType picks one release type off a matched AcoustID result's
// releasegroups list. A recording can belong to many release groups (its
// original album plus every compilation/reissue it's ever appeared on),
// so blindly preferring "Compilation" mislabels most well-known songs.
// Instead: if taggedAlbum is set, prefer the group whose title matches it
// (case-insensitively); otherwise fall back to the first group in the
// list, which AcoustID/MusicBrainz orders with the original release
// first. Returns lowercase to match the Vorbis `releasetype` convention.
func pickReleaseType(groups []releaseGroup, taggedAlbum string) string {
	if taggedAlbum != "" {
		for _, group := range groups {
			if strings.EqualFold(strings.TrimSpace(group.Title), strings.TrimSpace(taggedAlbum)) {
				if containsFold(group.SecondaryTypes, "Compilation") {
					return "compilation"
				}
				if group.Type != "" {
					return strings.ToLower(group.Type)
				}
			}
		}
	}
	for _, group := range groups {
		if group.Type != "" {
			return strings.ToLower(group.Type)
		}
	}
	return ""
}

// pickReleaseProvenance picks one release off a recording's MusicBrainz
// releases list and returns (releaseType, date, originalDate): date is
// that release's own release date; originalDate is its release-group's
// first-release-date; releaseType is "compilation" if the release-group
// carries that secondary type, else its primary type. Mirrors
// pickReleaseType's matching logic: prefer the release whose title
// matches taggedAlbum, otherwise fall back to the first release.
func pickReleaseProvenance(releases []mbRelease, taggedAlbum string) (releaseType, date, originalDate string) {
	extract := func(r mbRelease) (string, string, string) {
		group := r.ReleaseGroup
		rt := ""
		if containsFold(group.SecondaryTypes, "Compilation") {
			rt = "compilation"
		} else if group.PrimaryType != "" {
			rt = strings.ToLower(group.PrimaryType)
		}
		return rt, r.Date, group.FirstReleaseDate
	}

	if taggedAlbum != "" {
		for _, r := range releases {
			if strings.EqualFold(strings.TrimSpace(r.Title), strings.TrimSpace(taggedAlbum)) {
				return extract(r)
			}
		}
	}
	if len(releases) > 0 {
		return extract(releases[0])
	}
	return "", "", ""
}
