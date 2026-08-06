package lidarr

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func noSleep(t *testing.T) {
	t.Helper()
	orig := sleepFunc
	sleepFunc = func(time.Duration) {}
	t.Cleanup(func() { sleepFunc = orig })
}

func fakeResponse(status int) *http.Response {
	return &http.Response{StatusCode: status, Body: http.NoBody}
}

func TestWithRetryReturnsFirstSuccess(t *testing.T) {
	calls := 0
	resp, err := withRetry(func() (*http.Response, error) {
		calls++
		return fakeResponse(200), nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if resp.StatusCode != 200 {
		t.Errorf("status = %d, want 200", resp.StatusCode)
	}
	if calls != 1 {
		t.Errorf("calls = %d, want 1", calls)
	}
}

func TestWithRetryRetriesTransportErrorsThenSucceeds(t *testing.T) {
	noSleep(t)
	calls := 0
	resp, err := withRetry(func() (*http.Response, error) {
		calls++
		if calls < 3 {
			return nil, errors.New("blip")
		}
		return fakeResponse(200), nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if resp.StatusCode != 200 {
		t.Errorf("status = %d, want 200", resp.StatusCode)
	}
	if calls != 3 {
		t.Errorf("calls = %d, want 3", calls)
	}
}

func TestWithRetryRetries5xxThenSucceeds(t *testing.T) {
	noSleep(t)
	calls := 0
	resp, err := withRetry(func() (*http.Response, error) {
		calls++
		if calls == 1 {
			return fakeResponse(503), nil
		}
		return fakeResponse(200), nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if resp.StatusCode != 200 {
		t.Errorf("status = %d, want 200", resp.StatusCode)
	}
	if calls != 2 {
		t.Errorf("calls = %d, want 2", calls)
	}
}

func TestWithRetryDoesNotRetry4xx(t *testing.T) {
	calls := 0
	resp, err := withRetry(func() (*http.Response, error) {
		calls++
		return fakeResponse(404), nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if resp.StatusCode != 404 {
		t.Errorf("status = %d, want 404", resp.StatusCode)
	}
	if calls != 1 {
		t.Errorf("calls = %d, want 1 (4xx must not retry)", calls)
	}
}

func TestWithRetryRaisesAfterExhaustingAttempts(t *testing.T) {
	noSleep(t)
	calls := 0
	_, err := withRetry(func() (*http.Response, error) {
		calls++
		return nil, errors.New("still down")
	})
	if err == nil {
		t.Fatal("expected error")
	}
	if calls != RetryAttempts {
		t.Errorf("calls = %d, want %d", calls, RetryAttempts)
	}
}

func TestCheckConnectionReturnsVersion(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Api-Key") != "key" {
			t.Errorf("X-Api-Key = %q, want key", r.Header.Get("X-Api-Key"))
		}
		w.Write([]byte(`{"version": "1.2.3"}`))
	}))
	defer srv.Close()

	version, err := CheckConnection(srv.URL, "key")
	if err != nil {
		t.Fatal(err)
	}
	if version != "1.2.3" {
		t.Errorf("version = %q, want 1.2.3", version)
	}
}

func TestCheckConnectionRaisesOnBadAPIKey(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
	}))
	defer srv.Close()

	_, err := CheckConnection(srv.URL, "wrong-key")
	if err == nil || !contains(err.Error(), "rejected the API key") {
		t.Errorf("err = %v, want message containing 'rejected the API key'", err)
	}
}

func TestCheckConnectionRaisesWhenUnreachable(t *testing.T) {
	noSleep(t)
	_, err := CheckConnection("http://127.0.0.1:1", "key")
	if err == nil || !contains(err.Error(), "Could not reach Lidarr") {
		t.Errorf("err = %v, want message containing 'Could not reach Lidarr'", err)
	}
}

func contains(s, substr string) bool {
	for i := 0; i+len(substr) <= len(s); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}

func TestRemapPathToLidarrRewritesMatchingPrefix(t *testing.T) {
	got := RemapPathToLidarr("/home/user/Music/Artist/Album", "/home/user/Music", "/music")
	want := "/music/Artist/Album"
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestRemapPathToLidarrLeavesPathUnchangedWhenRootsBlank(t *testing.T) {
	got := RemapPathToLidarr("/home/user/Music/Artist/Album", "", "")
	if got != "/home/user/Music/Artist/Album" {
		t.Errorf("got %q, want unchanged", got)
	}
}

func TestRemapPathToLidarrLeavesPathUnchangedWhenNotUnderLocalRoot(t *testing.T) {
	got := RemapPathToLidarr("/somewhere/else/Album", "/home/user/Music", "/music")
	if got != "/somewhere/else/Album" {
		t.Errorf("got %q, want unchanged", got)
	}
}

func TestLidarrPathToLocalRewritesMatchingPrefix(t *testing.T) {
	got := LidarrPathToLocal("/music/Artist/Album", "/home/user/Music", "/music")
	want := "/home/user/Music/Artist/Album"
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestLidarrPathToLocalLeavesPathUnchangedWhenRootsBlank(t *testing.T) {
	got := LidarrPathToLocal("/music/Artist/Album", "", "")
	if got != "/music/Artist/Album" {
		t.Errorf("got %q, want unchanged", got)
	}
}

func TestDeleteTrackfileTreats404AsAlreadyGone(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}))
	defer srv.Close()

	if err := DeleteTrackfile(srv.URL, "key", 20050); err != nil {
		t.Errorf("expected no error on 404, got %v", err)
	}
}

func TestDeleteTrackfileRaisesOnOtherHTTPErrors(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
	}))
	defer srv.Close()

	err := DeleteTrackfile(srv.URL, "key", 20050)
	if err == nil || !contains(err.Error(), "Failed to delete") {
		t.Errorf("err = %v, want message containing 'Failed to delete'", err)
	}
}
