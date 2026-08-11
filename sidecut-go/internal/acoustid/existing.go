package acoustid

import (
	"path/filepath"
	"strings"

	"github.com/bogem/id3v2/v2"

	"sidecut-go/internal/flactag"
)

const (
	recordingIDKey = "musicbrainz_trackid"
	ufidOwner      = "http://musicbrainz.org"
)

type existingRecording struct {
	recordingID string
	artist      string
	title       string
	album       string
}

// readExistingRecording reads the existing MusicBrainz recording ID (if
// any) plus artist/title/album, from either a FLAC (Vorbis comments) or
// an MP3 (ID3v2) file, so Check can compare against whichever format it's
// given. Mirrors core.py's _read_existing_recording.
func readExistingRecording(path string) (existingRecording, error) {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".flac":
		return readExistingRecordingFromFLAC(path)
	case ".mp3":
		return readExistingRecordingFromMP3(path)
	default:
		return existingRecording{}, nil
	}
}

func readExistingRecordingFromFLAC(path string) (existingRecording, error) {
	tags, err := flactag.Read(path)
	if err != nil {
		return existingRecording{}, err
	}
	var recordingID string
	if values := tags.Get(recordingIDKey); len(values) > 0 {
		recordingID = values[0]
	}
	return existingRecording{
		recordingID: recordingID,
		artist:      strings.Join(tags.Get("artist"), "; "),
		title:       strings.Join(tags.Get("title"), "; "),
		album:       strings.Join(tags.Get("album"), "; "),
	}, nil
}

func readExistingRecordingFromMP3(path string) (existingRecording, error) {
	tag, err := id3v2.Open(path, id3v2.Options{Parse: true})
	if err != nil {
		return existingRecording{}, err
	}
	defer tag.Close()

	var recordingID string
	for _, f := range tag.GetFrames(tag.CommonID("Unique file identifier")) {
		if ufid, ok := f.(id3v2.UFIDFrame); ok && ufid.OwnerIdentifier == ufidOwner {
			recordingID = string(ufid.Identifier)
			break
		}
	}
	return existingRecording{
		recordingID: recordingID,
		artist:      tag.Artist(),
		title:       tag.Title(),
		album:       tag.Album(),
	}, nil
}
