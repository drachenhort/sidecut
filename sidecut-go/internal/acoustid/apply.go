package acoustid

import (
	"fmt"
	"path/filepath"
	"strings"

	"github.com/bogem/id3v2/v2"

	"sidecut-go/internal/flactag"
)

// releaseTypeDesc is the TXXX Description Picard/core.py's copy_tags uses
// for the `releasetype` Vorbis comment - must match core/tags.go's
// freetextDescriptions["releasetype"] so a value applied here reads back
// correctly through the same mapping.
const releaseTypeDesc = "MusicBrainz Album Type"

// ApplyReleaseType writes check's release type onto path if it has one
// and path has no release-type tag yet: the `releasetype` Vorbis comment
// on a FLAC, or the equivalent TXXX:MusicBrainz Album Type ID3 frame on
// an already-converted MP3. Never overwrites an existing value - only
// fills in a gap. Mirrors core.py's apply_release_type.
func ApplyReleaseType(path string, check Check) (bool, error) {
	if check.ReleaseType == "" {
		return false, nil
	}
	switch strings.ToLower(filepath.Ext(path)) {
	case ".flac":
		tags, err := flactag.Read(path)
		if err != nil {
			return false, err
		}
		if len(tags.Get("releasetype")) > 0 {
			return false, nil
		}
		if err := flactag.SetComments(path, []flactag.Comment{{Key: "releasetype", Value: check.ReleaseType}}); err != nil {
			return false, err
		}
		return true, nil
	case ".mp3":
		tag, err := id3v2.Open(path, id3v2.Options{Parse: true})
		if err != nil {
			return false, err
		}
		defer tag.Close()
		if findTXXX(tag, releaseTypeDesc) != "" {
			return false, nil
		}
		tag.AddUserDefinedTextFrame(id3v2.UserDefinedTextFrame{
			Encoding: id3v2.EncodingUTF8, Description: releaseTypeDesc, Value: check.ReleaseType,
		})
		tag.SetVersion(3)
		if err := tag.Save(); err != nil {
			return false, err
		}
		return true, nil
	default:
		return false, nil
	}
}

func findTXXX(tag *id3v2.Tag, desc string) string {
	for _, f := range tag.GetFrames(tag.CommonID("TXXX")) {
		if udtf, ok := f.(id3v2.UserDefinedTextFrame); ok && udtf.Description == desc {
			return udtf.Value
		}
	}
	return ""
}

// ApplyReleaseProvenance writes check's date and/or originaldate onto
// path, wherever path is missing that field: the "date"/"originaldate"
// Vorbis comments on a FLAC, or the TDRC/TDOR ID3 frames on an
// already-converted MP3. Each field is filled independently and never
// overwrites an existing value, mirroring ApplyReleaseType. Returns
// whether anything was written. Mirrors core.py's apply_release_provenance.
func ApplyReleaseProvenance(path string, check Check) (bool, error) {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".flac":
		tags, err := flactag.Read(path)
		if err != nil {
			return false, err
		}
		var updates []flactag.Comment
		if check.Date != "" && len(tags.Get("date")) == 0 {
			updates = append(updates, flactag.Comment{Key: "date", Value: check.Date})
		}
		if check.OriginalDate != "" && len(tags.Get("originaldate")) == 0 {
			updates = append(updates, flactag.Comment{Key: "originaldate", Value: check.OriginalDate})
		}
		if len(updates) == 0 {
			return false, nil
		}
		if err := flactag.SetComments(path, updates); err != nil {
			return false, err
		}
		return true, nil
	case ".mp3":
		tag, err := id3v2.Open(path, id3v2.Options{Parse: true})
		if err != nil {
			return false, err
		}
		defer tag.Close()
		wrote := false
		if check.Date != "" && tag.GetTextFrame("TDRC").Text == "" {
			tag.AddTextFrame("TDRC", id3v2.EncodingUTF8, check.Date)
			wrote = true
		}
		if check.OriginalDate != "" && tag.GetTextFrame("TDOR").Text == "" {
			tag.AddTextFrame("TDOR", id3v2.EncodingUTF8, check.OriginalDate)
			wrote = true
		}
		if !wrote {
			return false, nil
		}
		tag.SetVersion(3)
		if err := tag.Save(); err != nil {
			return false, err
		}
		return true, nil
	default:
		return false, nil
	}
}

// CorrectAcoustIDMismatch, if check is a confident enough mismatch,
// rewrites the FLAC's musicbrainz_trackid tag to AcoustID's suggested
// recording and marks check as corrected (mutating it in place so
// callers logging/emitting check afterwards see the update). Returns
// whether it corrected anything. Only touches musicbrainz_trackid -
// artist/title/etc. are left alone since AcoustID's formatting may not
// match the file's convention. Only ever touches FLACs: it's meant to
// run just before conversion, not on already-converted MP3s. Mirrors
// core.py's correct_acoustid_mismatch.
func CorrectAcoustIDMismatch(path string, check *Check, minScore float64) (bool, error) {
	if strings.ToLower(filepath.Ext(path)) != ".flac" {
		return false, nil
	}
	if check.Status != "mismatch" || check.RecordingID == "" {
		return false, nil
	}
	if check.HasScore && check.Score < minScore {
		return false, nil
	}
	if err := flactag.SetComments(path, []flactag.Comment{{Key: recordingIDKey, Value: check.RecordingID}}); err != nil {
		return false, err
	}
	check.Corrected = true
	check.Detail += fmt.Sprintf(" — corrected %s tag", recordingIDKey)
	return true, nil
}
