// Package flactag reads the FLAC metadata blocks core.py's copy_tags needs:
// STREAMINFO (for duration), VORBIS_COMMENT (preserving every value for a
// repeated key, not just the last one), and every PICTURE block (not just
// one). dhowden/tag - the obvious off-the-shelf choice - collapses both of
// those to a single value, silently dropping data mutagen preserves, so
// this is hand-rolled instead of pulling in a lossy dependency.
package flactag

import (
	"bufio"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"os"
	"time"
)

type blockType byte

const (
	blockStreamInfo    blockType = 0
	blockVorbisComment blockType = 4
	blockPicture       blockType = 6
)

// Picture mirrors mutagen's FLAC Picture fields that core.py's
// _copy_pictures reads (picture.type/desc/mime/data).
type Picture struct {
	Type int // ID3 APIC picture type byte, same enum FLAC/ID3 share
	MIME string
	Desc string
	Data []byte
}

// Tags holds every Vorbis comment value (in file order, duplicates kept)
// plus every embedded picture and the stream duration.
type Tags struct {
	// Comments preserves original casing and order; core.py/mutagen
	// lower-cases keys for lookup but keeps original values as-is.
	Comments []Comment
	Pictures []Picture
	Duration time.Duration
}

type Comment struct {
	Key   string
	Value string
}

// Get returns every value for a case-insensitively matched key, in file
// order - the equivalent of mutagen's FLAC[key] returning a list.
func (t Tags) Get(key string) []string {
	var values []string
	for _, c := range t.Comments {
		if equalFold(c.Key, key) {
			values = append(values, c.Value)
		}
	}
	return values
}

func equalFold(a, b string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		ca, cb := a[i], b[i]
		if 'A' <= ca && ca <= 'Z' {
			ca += 'a' - 'A'
		}
		if 'A' <= cb && cb <= 'Z' {
			cb += 'a' - 'A'
		}
		if ca != cb {
			return false
		}
	}
	return true
}

// Read parses the FLAC metadata blocks (not the audio frames) from path.
func Read(path string) (Tags, error) {
	f, err := os.Open(path)
	if err != nil {
		return Tags{}, err
	}
	defer f.Close()

	r := bufio.NewReader(f)
	magic := make([]byte, 4)
	if _, err := io.ReadFull(r, magic); err != nil {
		return Tags{}, err
	}
	if string(magic) != "fLaC" {
		return Tags{}, errors.New("flactag: not a FLAC file (missing fLaC marker)")
	}

	var tags Tags
	for {
		header := make([]byte, 4)
		if _, err := io.ReadFull(r, header); err != nil {
			return Tags{}, fmt.Errorf("flactag: reading block header: %w", err)
		}
		last := header[0]&0x80 != 0
		bt := blockType(header[0] & 0x7f)
		length := int(header[1])<<16 | int(header[2])<<8 | int(header[3])

		body := make([]byte, length)
		if _, err := io.ReadFull(r, body); err != nil {
			return Tags{}, fmt.Errorf("flactag: reading block body: %w", err)
		}

		switch bt {
		case blockStreamInfo:
			tags.Duration = parseStreamInfoDuration(body)
		case blockVorbisComment:
			comments, err := parseVorbisComment(body)
			if err != nil {
				return Tags{}, fmt.Errorf("flactag: parsing VORBIS_COMMENT: %w", err)
			}
			tags.Comments = comments
		case blockPicture:
			pic, err := parsePicture(body)
			if err != nil {
				return Tags{}, fmt.Errorf("flactag: parsing PICTURE: %w", err)
			}
			tags.Pictures = append(tags.Pictures, pic)
		}

		if last {
			break
		}
	}
	return tags, nil
}

func parseStreamInfoDuration(body []byte) time.Duration {
	// STREAMINFO layout: 16+16 bits block size, 24+24 bits frame size,
	// 20 bits sample rate, 3 bits channels-1, 5 bits bits-per-sample-1,
	// 36 bits total samples, 128 bits MD5 - total samples/sample rate
	// starts at byte 10, spans bits we pull out of a 8-byte big-endian
	// window (18 bytes in: sample rate(20) | channels(3) | bps(5) | totalsamples(36)).
	if len(body) < 18 {
		return 0
	}
	bits := binary.BigEndian.Uint64(body[10:18])
	sampleRate := uint32(bits >> 44)   // top 20 bits of this 64-bit window
	totalSamples := bits & 0xFFFFFFFFF // bottom 36 bits
	if sampleRate == 0 {
		return 0
	}
	seconds := float64(totalSamples) / float64(sampleRate)
	return time.Duration(seconds * float64(time.Second))
}

func parseVorbisComment(body []byte) ([]Comment, error) {
	r := &byteReader{b: body}
	vendorLen, err := r.uint32LE()
	if err != nil {
		return nil, err
	}
	if _, err := r.take(int(vendorLen)); err != nil { // vendor string, unused
		return nil, err
	}
	count, err := r.uint32LE()
	if err != nil {
		return nil, err
	}
	comments := make([]Comment, 0, count)
	for i := uint32(0); i < count; i++ {
		entryLen, err := r.uint32LE()
		if err != nil {
			return nil, err
		}
		entry, err := r.take(int(entryLen))
		if err != nil {
			return nil, err
		}
		key, value, ok := splitComment(string(entry))
		if !ok {
			continue
		}
		comments = append(comments, Comment{Key: key, Value: value})
	}
	return comments, nil
}

func splitComment(s string) (key, value string, ok bool) {
	for i := 0; i < len(s); i++ {
		if s[i] == '=' {
			return s[:i], s[i+1:], true
		}
	}
	return "", "", false
}

func parsePicture(body []byte) (Picture, error) {
	r := &byteReader{b: body}
	picType, err := r.uint32BE()
	if err != nil {
		return Picture{}, err
	}
	mimeLen, err := r.uint32BE()
	if err != nil {
		return Picture{}, err
	}
	mime, err := r.take(int(mimeLen))
	if err != nil {
		return Picture{}, err
	}
	descLen, err := r.uint32BE()
	if err != nil {
		return Picture{}, err
	}
	desc, err := r.take(int(descLen))
	if err != nil {
		return Picture{}, err
	}
	// width, height, colorDepth, colorsUsed - unused
	if _, err := r.take(16); err != nil {
		return Picture{}, err
	}
	dataLen, err := r.uint32BE()
	if err != nil {
		return Picture{}, err
	}
	data, err := r.take(int(dataLen))
	if err != nil {
		return Picture{}, err
	}
	return Picture{
		Type: int(picType),
		MIME: string(mime),
		Desc: string(desc),
		Data: append([]byte(nil), data...),
	}, nil
}

type byteReader struct {
	b   []byte
	pos int
}

func (r *byteReader) take(n int) ([]byte, error) {
	if r.pos+n > len(r.b) {
		return nil, io.ErrUnexpectedEOF
	}
	v := r.b[r.pos : r.pos+n]
	r.pos += n
	return v, nil
}

func (r *byteReader) uint32LE() (uint32, error) {
	b, err := r.take(4)
	if err != nil {
		return 0, err
	}
	return binary.LittleEndian.Uint32(b), nil
}

func (r *byteReader) uint32BE() (uint32, error) {
	b, err := r.take(4)
	if err != nil {
		return 0, err
	}
	return binary.BigEndian.Uint32(b), nil
}
