package id3spike

import (
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/bogem/id3v2/v2"

	"sidecut-go/internal/flactag"
)

// TestSpikeConvertAndWriteTags proves the ffmpeg-convert + flactag-read +
// id3v2-write + id3v2-read-back round trip works before committing to
// porting core.py's copy_tags on top of these libraries for real.
func TestSpikeConvertAndWriteTags(t *testing.T) {
	src := "/home/sigma/git/flac2mp3/test-flac/Iron Man 2 (2010)/AC+DC - Iron Man 2 - 11 - Have a Drink on Me.flac"
	dir := t.TempDir()
	dst := filepath.Join(dir, "out.mp3")

	cmd := exec.Command("ffmpeg", "-y", "-nostdin", "-loglevel", "error",
		"-i", src, "-map", "0:a", "-map_metadata", "-1", "-codec:a", "libmp3lame", "-q:a", "0", dst)
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("ffmpeg failed: %v\n%s", err, out)
	}

	flacTags, err := flactag.Read(src)
	if err != nil {
		t.Fatal(err)
	}
	if len(flacTags.Pictures) == 0 {
		t.Fatal("expected sample file to have embedded art")
	}

	tag, err := id3v2.Open(dst, id3v2.Options{Parse: false})
	if err != nil {
		t.Fatal(err)
	}
	defer tag.Close()
	tag.SetVersion(3)
	tag.SetDefaultEncoding(id3v2.EncodingUTF8)

	// Mirrors core.py's copy_tags: TXXX frames must have a unique
	// Description per the ID3v2 spec, so a repeated Vorbis comment key
	// (e.g. multiple GENRE entries) gets joined into one "; "-separated
	// frame rather than one frame per value.
	seen := map[string]bool{}
	wantGenres := flacTags.Get("GENRE")
	for _, c := range flacTags.Comments {
		if seen[c.Key] {
			continue
		}
		seen[c.Key] = true
		joined := joinValues(flacTags.Get(c.Key), "; ")

		switch c.Key {
		case "TITLE":
			tag.SetTitle(joined)
		case "ARTIST":
			tag.SetArtist(joined)
		case "ALBUM":
			tag.SetAlbum(joined)
		case "MUSICBRAINZ_TRACKID":
			tag.AddUFIDFrame(id3v2.UFIDFrame{
				OwnerIdentifier: "http://musicbrainz.org",
				Identifier:      []byte(joined),
			})
		default:
			tag.AddUserDefinedTextFrame(id3v2.UserDefinedTextFrame{
				Encoding:    id3v2.EncodingUTF8,
				Description: c.Key,
				Value:       joined,
			})
		}
	}
	for _, p := range flacTags.Pictures {
		tag.AddAttachedPicture(id3v2.PictureFrame{
			Encoding:    id3v2.EncodingUTF8,
			MimeType:    p.MIME,
			PictureType: byte(p.Type),
			Description: p.Desc,
			Picture:     p.Data,
		})
	}

	if err := tag.Save(); err != nil {
		t.Fatal(err)
	}
	tag.Close()

	readBack, err := id3v2.Open(dst, id3v2.Options{Parse: true})
	if err != nil {
		t.Fatal(err)
	}
	defer readBack.Close()

	if got := readBack.Title(); got != "Have a Drink on Me" {
		t.Errorf("Title = %q, want %q", got, "Have a Drink on Me")
	}
	if got := readBack.Artist(); got != "AC/DC" {
		t.Errorf("Artist = %q, want %q", got, "AC/DC")
	}

	var gotGenre string
	for _, f := range readBack.GetFrames(readBack.CommonID("TXXX")) {
		udtf, ok := f.(id3v2.UserDefinedTextFrame)
		if ok && udtf.Description == "GENRE" {
			gotGenre = udtf.Value
		}
	}
	if want := joinValues(wantGenres, "; "); gotGenre != want {
		t.Errorf("GENRE TXXX frame = %q, want %q", gotGenre, want)
	}

	ufids := readBack.GetFrames(readBack.CommonID("Unique file identifier"))
	if len(ufids) != 1 {
		t.Fatalf("expected 1 UFID frame, got %d", len(ufids))
	}
	if ufid, ok := ufids[0].(id3v2.UFIDFrame); !ok || ufid.OwnerIdentifier != "http://musicbrainz.org" {
		t.Errorf("UFID frame wrong: %+v", ufids[0])
	}

	pics := readBack.GetFrames(readBack.CommonID("Attached picture"))
	if len(pics) != len(flacTags.Pictures) {
		t.Errorf("APIC frame count = %d, want %d", len(pics), len(flacTags.Pictures))
	}
	if pic, ok := pics[0].(id3v2.PictureFrame); !ok || len(pic.Picture) != len(flacTags.Pictures[0].Data) {
		t.Errorf("APIC picture data size mismatch")
	}

	t.Logf("round trip OK: %d comments, genre=%q, %d pictures", len(flacTags.Comments), gotGenre, len(pics))
}

func joinValues(values []string, sep string) string {
	out := ""
	for i, v := range values {
		if i > 0 {
			out += sep
		}
		out += v
	}
	return out
}
