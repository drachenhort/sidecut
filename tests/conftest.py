import pytest

import core


@pytest.fixture(autouse=True)
def _no_real_rate_limiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared AcoustID/MusicBrainz rate limiters are module-level
    singletons, so without this every test in the suite would pay their
    real (up to 1s, for MusicBrainz) delay on top of each other. Tests
    that exercise _RateLimiter itself construct their own instance and are
    unaffected; tests that need the real shared-limiter behavior
    monkeypatch it back themselves."""
    monkeypatch.setattr(core._acoustid_rate_limiter, "wait", lambda: None)
    monkeypatch.setattr(core._musicbrainz_rate_limiter, "wait", lambda: None)
