package acoustid

import (
	"testing"
	"time"
)

func TestRateLimiterSpacesCallsApart(t *testing.T) {
	rl := newRateLimiter(20) // min interval 50ms

	start := time.Now()
	for i := 0; i < 3; i++ {
		rl.wait()
	}
	elapsed := time.Since(start)

	if elapsed < 90*time.Millisecond {
		t.Errorf("elapsed = %v, want >= ~100ms (2 gaps of 50ms)", elapsed)
	}
}

func TestRateLimiterDoesNotBlockFirstCall(t *testing.T) {
	rl := newRateLimiter(1)

	start := time.Now()
	rl.wait()
	elapsed := time.Since(start)

	if elapsed > 10*time.Millisecond {
		t.Errorf("first call blocked for %v, want ~0", elapsed)
	}
}
