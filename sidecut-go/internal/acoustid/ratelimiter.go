package acoustid

import (
	"sync"
	"time"
)

// rateLimiter caps calls to at most perSecond, blocking the calling
// goroutine as needed. Safe for concurrent use, so a single instance
// shared across worker goroutines enforces a global rate regardless of
// how many run concurrently. Mirrors core.py's _RateLimiter.
type rateLimiter struct {
	minInterval time.Duration
	mu          sync.Mutex
	lastCall    time.Time
}

func newRateLimiter(perSecond float64) *rateLimiter {
	return &rateLimiter{minInterval: time.Duration(float64(time.Second) / perSecond)}
}

func (r *rateLimiter) wait() {
	r.mu.Lock()
	defer r.mu.Unlock()
	if !r.lastCall.IsZero() {
		if sleep := r.minInterval - time.Since(r.lastCall); sleep > 0 {
			time.Sleep(sleep)
		}
	}
	r.lastCall = time.Now()
}
