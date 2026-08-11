package acoustid

import "testing"

func TestPickReleaseTypePrefersGroupMatchingTaggedAlbum(t *testing.T) {
	groups := []releaseGroup{
		{Title: "Original Album", Type: "Album"},
		{Title: "Greatest Hits", Type: "Album", SecondaryTypes: []string{"Compilation"}},
	}

	got := pickReleaseType(groups, "Greatest Hits")

	if got != "compilation" {
		t.Errorf("got %q, want %q", got, "compilation")
	}
}

func TestPickReleaseTypeFallsBackToFirstGroupWithoutAlbumMatch(t *testing.T) {
	groups := []releaseGroup{
		{Title: "Original Album", Type: "Album"},
		{Title: "Greatest Hits", Type: "Album", SecondaryTypes: []string{"Compilation"}},
	}

	got := pickReleaseType(groups, "")

	if got != "album" {
		t.Errorf("got %q, want %q", got, "album")
	}
}

func TestPickReleaseTypeNoneWithoutGroups(t *testing.T) {
	got := pickReleaseType(nil, "")

	if got != "" {
		t.Errorf("got %q, want empty", got)
	}
}

func TestPickReleaseProvenancePrefersReleaseMatchingTaggedAlbum(t *testing.T) {
	releases := []mbRelease{
		{Title: "Original Album", Date: "1980-01-01", ReleaseGroup: mbReleaseGroup{PrimaryType: "Album", FirstReleaseDate: "1980-01-01"}},
		{Title: "Greatest Hits", Date: "1999-01-01", ReleaseGroup: mbReleaseGroup{PrimaryType: "Album", SecondaryTypes: []string{"Compilation"}, FirstReleaseDate: "1999-01-01"}},
	}

	releaseType, date, originalDate := pickReleaseProvenance(releases, "Greatest Hits")

	if releaseType != "compilation" || date != "1999-01-01" || originalDate != "1999-01-01" {
		t.Errorf("got (%q, %q, %q)", releaseType, date, originalDate)
	}
}

func TestPickReleaseProvenanceFallsBackToFirstRelease(t *testing.T) {
	releases := []mbRelease{
		{Title: "Iron Man 2", Date: "2011-06-01", ReleaseGroup: mbReleaseGroup{PrimaryType: "Album", SecondaryTypes: []string{"Compilation"}, FirstReleaseDate: "1980-07-25"}},
	}

	releaseType, date, originalDate := pickReleaseProvenance(releases, "")

	if releaseType != "compilation" || date != "2011-06-01" || originalDate != "1980-07-25" {
		t.Errorf("got (%q, %q, %q)", releaseType, date, originalDate)
	}
}

func TestPickReleaseProvenanceNoneWithoutReleases(t *testing.T) {
	releaseType, date, originalDate := pickReleaseProvenance(nil, "")

	if releaseType != "" || date != "" || originalDate != "" {
		t.Errorf("got (%q, %q, %q), want all empty", releaseType, date, originalDate)
	}
}
