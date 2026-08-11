package flactag

import (
	"bufio"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

type rawBlock struct {
	typ  blockType
	body []byte
}

// readRaw parses every metadata block from path without discarding any of
// them (Read only keeps STREAMINFO/VORBIS_COMMENT/PICTURE bodies it
// understands - the writer needs every block, understood or not, to
// round-trip the file unchanged apart from the one it's editing), plus
// everything after the metadata blocks (the audio frames).
func readRaw(path string) (blocks []rawBlock, audio []byte, err error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, nil, err
	}
	defer f.Close()

	r := bufio.NewReader(f)
	magic := make([]byte, 4)
	if _, err := io.ReadFull(r, magic); err != nil {
		return nil, nil, err
	}
	if string(magic) != "fLaC" {
		return nil, nil, errors.New("flactag: not a FLAC file (missing fLaC marker)")
	}

	for {
		header := make([]byte, 4)
		if _, err := io.ReadFull(r, header); err != nil {
			return nil, nil, fmt.Errorf("flactag: reading block header: %w", err)
		}
		last := header[0]&0x80 != 0
		bt := blockType(header[0] & 0x7f)
		length := int(header[1])<<16 | int(header[2])<<8 | int(header[3])

		body := make([]byte, length)
		if _, err := io.ReadFull(r, body); err != nil {
			return nil, nil, fmt.Errorf("flactag: reading block body: %w", err)
		}
		blocks = append(blocks, rawBlock{typ: bt, body: body})

		if last {
			break
		}
	}

	audio, err = io.ReadAll(r)
	if err != nil {
		return nil, nil, fmt.Errorf("flactag: reading audio data: %w", err)
	}
	return blocks, audio, nil
}

// SetComments rewrites path's VORBIS_COMMENT block: for each update, every
// existing value for its Key (matched case-insensitively) is removed and
// replaced with the update's single Value, appended after every
// surviving comment. Every other comment, every other metadata block
// (STREAMINFO, PICTUREs, ...), and the audio data are copied through
// unchanged. Mirrors mutagen's `FLAC(path)[key] = [value]; tags.save()`.
func SetComments(path string, updates []Comment) error {
	blocks, audio, err := readRaw(path)
	if err != nil {
		return err
	}

	idx := -1
	for i, b := range blocks {
		if b.typ == blockVorbisComment {
			idx = i
			break
		}
	}

	var vendor string
	var comments []Comment
	if idx >= 0 {
		vendor, comments, err = parseVorbisCommentFull(blocks[idx].body)
		if err != nil {
			return fmt.Errorf("flactag: parsing VORBIS_COMMENT: %w", err)
		}
	}

	for _, u := range updates {
		filtered := comments[:0:0]
		for _, c := range comments {
			if !equalFold(c.Key, u.Key) {
				filtered = append(filtered, c)
			}
		}
		comments = append(filtered, Comment{Key: u.Key, Value: u.Value})
	}

	newBody := serializeVorbisComment(vendor, comments)
	if idx >= 0 {
		blocks[idx].body = newBody
	} else {
		// STREAMINFO must stay first; insert right after it.
		insertAt := 1
		if len(blocks) == 0 {
			insertAt = 0
		}
		blocks = append(blocks, rawBlock{})
		copy(blocks[insertAt+1:], blocks[insertAt:])
		blocks[insertAt] = rawBlock{typ: blockVorbisComment, body: newBody}
	}

	return writeRaw(path, blocks, audio)
}

func serializeVorbisComment(vendor string, comments []Comment) []byte {
	var buf []byte
	buf = appendUint32LE(buf, uint32(len(vendor)))
	buf = append(buf, vendor...)
	buf = appendUint32LE(buf, uint32(len(comments)))
	for _, c := range comments {
		entry := c.Key + "=" + c.Value
		buf = appendUint32LE(buf, uint32(len(entry)))
		buf = append(buf, entry...)
	}
	return buf
}

func appendUint32LE(buf []byte, v uint32) []byte {
	var b [4]byte
	binary.LittleEndian.PutUint32(b[:], v)
	return append(buf, b[:]...)
}

// writeRaw writes "fLaC" + every block (with is-last recomputed so only
// the final one carries the bit) + audio to a temp file in path's
// directory, then renames it over path - so a crash mid-write can never
// leave a truncated or half-written file in place.
func writeRaw(path string, blocks []rawBlock, audio []byte) error {
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, ".flactag-*.tmp")
	if err != nil {
		return err
	}
	tmpPath := tmp.Name()
	defer os.Remove(tmpPath) // no-op once the rename below succeeds

	w := bufio.NewWriter(tmp)
	if _, err := w.WriteString("fLaC"); err != nil {
		tmp.Close()
		return err
	}
	for i, b := range blocks {
		last := i == len(blocks)-1
		header := byte(b.typ)
		if last {
			header |= 0x80
		}
		length := len(b.body)
		if _, err := w.Write([]byte{header, byte(length >> 16), byte(length >> 8), byte(length)}); err != nil {
			tmp.Close()
			return err
		}
		if _, err := w.Write(b.body); err != nil {
			tmp.Close()
			return err
		}
	}
	if _, err := w.Write(audio); err != nil {
		tmp.Close()
		return err
	}
	if err := w.Flush(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpPath, path)
}
