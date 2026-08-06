package core

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/bogem/id3v2/v2"

	"sidecut-go/internal/flactag"
)

// standardFrames maps a lowercase Vorbis comment key to the ID3v2 text
// frame ID Picard uses for it (picard/formats/id3.py), so files this tool
// tags round-trip through Picard identically to ones it wrote itself.
// Matches core.py's _STANDARD_FRAME_BUILDERS.
var standardFrames = map[string]string{
	"title":            "TIT2",
	"subtitle":         "TIT3",
	"grouping":         "TIT1",
	"artist":           "TPE1",
	"album":            "TALB",
	"albumartist":      "TPE2",
	"discsubtitle":     "TSST",
	"conductor":        "TPE3",
	"remixer":          "TPE4",
	"lyricist":         "TEXT",
	"composer":         "TCOM",
	"encodedby":        "TENC",
	"date":             "TDRC",
	"originaldate":     "TDOR",
	"releasedate":      "TDRL",
	"genre":            "TCON",
	"tracknumber":      "TRCK",
	"discnumber":       "TPOS",
	"isrc":             "TSRC",
	"bpm":              "TBPM",
	"key":              "TKEY",
	"language":         "TLAN",
	"media":            "TMED",
	"mood":             "TMOO",
	"copyright":        "TCOP",
	"label":            "TPUB",
	"encodersettings":  "TSSE",
	"albumsort":        "TSOA",
	"artistsort":       "TSOP",
	"titlesort":        "TSOT",
	"albumartistsort":  "TSO2",
	"composersort":     "TSOC",
	"originalalbum":    "TOAL",
	"originalartist":   "TOPE",
	"originalfilename": "TOFN",
	"compilation":      "TCMP",
	"movement":         "MVNM",
}

// urlFrames maps a lowercase Vorbis comment key to a URL-link ID3 frame
// (WOAR/WCOP), which - unlike the text frames above - have no text
// encoding byte, just the raw URL.
var urlFrames = map[string]string{
	"website": "WOAR",
	"license": "WCOP",
}

const commentKey = "comment"

// recordingIDKey is the Vorbis comment key (the MusicBrainz recording ID)
// that Picard writes as a UFID frame instead of a TXXX frame.
const recordingIDKey = "musicbrainz_trackid"
const ufidOwner = "http://musicbrainz.org"

// freetextDescriptions maps a lowercase Vorbis comment key to the TXXX
// Description Picard uses when it differs from the raw tag name. Anything
// not listed here (and not a standard/url frame above) is kept verbatim
// as TXXX:<original-case tag name>, so no tag is ever silently dropped.
var freetextDescriptions = map[string]string{
	"musicbrainz_artistid":         "MusicBrainz Artist Id",
	"musicbrainz_albumid":          "MusicBrainz Album Id",
	"musicbrainz_albumartistid":    "MusicBrainz Album Artist Id",
	"releasetype":                  "MusicBrainz Album Type",
	"releasestatus":                "MusicBrainz Album Status",
	"musicbrainz_trmid":            "MusicBrainz TRM Id",
	"musicbrainz_releasetrackid":   "MusicBrainz Release Track Id",
	"musicbrainz_discid":           "MusicBrainz Disc Id",
	"musicbrainz_workid":           "MusicBrainz Work Id",
	"musicbrainz_composerid":       "MusicBrainz Composer Id",
	"musicbrainz_releasegroupid":   "MusicBrainz Release Group Id",
	"musicbrainz_originalalbumid":  "MusicBrainz Original Album Id",
	"musicbrainz_originalartistid": "MusicBrainz Original Artist Id",
	"releasecountry":               "MusicBrainz Album Release Country",
	"musicip_puid":                 "MusicIP PUID",
	"musicip_fingerprint":          "MusicMagic Fingerprint",
	"acoustid_fingerprint":         "Acoustid Fingerprint",
	"acoustid_id":                  "Acoustid Id",
	"writer":                       "Writer",
}

// CopyTags copies every FLAC Vorbis comment and embedded picture onto the
// MP3 at dst, mapping tag keys to the same ID3v2.3 frames MusicBrainz
// Picard would write. Mirrors core.py's copy_tags.
func CopyTags(src, dst string) error {
	flacTags, err := flactag.Read(src)
	if err != nil {
		return fmt.Errorf("reading FLAC tags: %w", err)
	}

	tag, err := id3v2.Open(dst, id3v2.Options{Parse: false})
	if err != nil {
		return fmt.Errorf("opening mp3 for tagging: %w", err)
	}
	defer tag.Close()
	tag.SetVersion(3)
	tag.SetDefaultEncoding(id3v2.EncodingUTF8)

	seen := map[string]bool{}
	for _, c := range flacTags.Comments {
		lowerKey := strings.ToLower(c.Key)
		if seen[lowerKey] {
			continue
		}
		seen[lowerKey] = true

		if lowerKey == recordingIDKey {
			values := flacTags.Get(c.Key)
			tag.AddUFIDFrame(id3v2.UFIDFrame{
				OwnerIdentifier: ufidOwner,
				Identifier:      []byte(asciiOnly(values[0])),
			})
			continue
		}

		joined := strings.Join(flacTags.Get(c.Key), "; ")

		if id, ok := standardFrames[lowerKey]; ok {
			tag.AddTextFrame(id, id3v2.EncodingUTF8, joined)
			continue
		}
		if id, ok := urlFrames[lowerKey]; ok {
			tag.AddFrame(id, id3v2.UnknownFrame{Body: []byte(joined)})
			continue
		}
		if lowerKey == commentKey {
			tag.AddCommentFrame(id3v2.CommentFrame{
				Encoding:    id3v2.EncodingUTF8,
				Language:    "eng",
				Description: "",
				Text:        joined,
			})
			continue
		}

		desc := c.Key
		if d, ok := freetextDescriptions[lowerKey]; ok {
			desc = d
		}
		tag.AddUserDefinedTextFrame(id3v2.UserDefinedTextFrame{
			Encoding:    id3v2.EncodingUTF8,
			Description: desc,
			Value:       joined,
		})
	}

	copyPictures(flacTags.Pictures, tag)

	return tag.Save()
}

func asciiOnly(s string) string {
	var b strings.Builder
	for _, r := range s {
		if r < 128 {
			b.WriteRune(r)
		}
	}
	return b.String()
}

// copyPictures mirrors core.py's _copy_pictures: APIC frames are keyed by
// (type, desc), so dedupe by appending an index to desc when two pictures
// share the same type/desc rather than silently overwriting one.
func copyPictures(pictures []flactag.Picture, tag *id3v2.Tag) {
	seen := map[string]bool{}
	for i, pic := range pictures {
		desc := pic.Desc
		key := strconv.Itoa(pic.Type) + "\x00" + desc
		if seen[key] {
			desc = strings.TrimSpace(fmt.Sprintf("%s %d", desc, i))
			key = strconv.Itoa(pic.Type) + "\x00" + desc
		}
		seen[key] = true
		tag.AddAttachedPicture(id3v2.PictureFrame{
			Encoding:    id3v2.EncodingUTF8,
			MimeType:    pic.MIME,
			PictureType: byte(pic.Type),
			Description: desc,
			Picture:     pic.Data,
		})
	}
}
