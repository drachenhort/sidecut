package acoustid

import (
	"encoding/json"
	"fmt"
	"os/exec"
)

// CheckFpcalc reports whether chromaprint's fpcalc is on PATH.
func CheckFpcalc() bool {
	_, err := exec.LookPath("fpcalc")
	return err == nil
}

func parseFpcalcOutput(data []byte) (duration int, fingerprint string, err error) {
	var body struct {
		Duration    float64 `json:"duration"`
		Fingerprint string  `json:"fingerprint"`
	}
	if err := json.Unmarshal(data, &body); err != nil {
		return 0, "", fmt.Errorf("parsing fpcalc output: %w", err)
	}
	return int(body.Duration), body.Fingerprint, nil
}

// fpcalcFingerprint runs chromaprint's fpcalc and returns
// (durationSeconds, fingerprint). Mirrors core.py's _fpcalc_fingerprint.
func fpcalcFingerprint(path string) (duration int, fingerprint string, err error) {
	out, err := exec.Command("fpcalc", "-json", path).Output()
	if err != nil {
		return 0, "", err
	}
	return parseFpcalcOutput(out)
}
