package acoustid

import "testing"

func TestParseFpcalcOutput(t *testing.T) {
	duration, fingerprint, err := parseFpcalcOutput([]byte(`{"duration": 245.3, "fingerprint": "AQAAT0mUaEk"}`))

	if err != nil {
		t.Fatal(err)
	}
	if duration != 245 {
		t.Errorf("duration = %d, want 245", duration)
	}
	if fingerprint != "AQAAT0mUaEk" {
		t.Errorf("fingerprint = %q, want %q", fingerprint, "AQAAT0mUaEk")
	}
}

func TestParseFpcalcOutputInvalidJSON(t *testing.T) {
	_, _, err := parseFpcalcOutput([]byte("not json"))

	if err == nil {
		t.Fatal("want error for invalid JSON")
	}
}
