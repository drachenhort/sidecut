import sys
from pathlib import Path

import pytest

# Add the project root to sys.path so local modules (config, core, etc.) are importable
# when running pytest from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import core


@pytest.fixture(autouse=True)
def _isolated_config_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Without this, any test that goes through config.load_settings()/
    config.resolve_config_path() without passing its own path (e.g. via
    lidarr_hook.py) would read/write the real ~/.config/flac2mp3/config.ini
    on the machine running the tests - or, since resolve_config_path()
    defaults a true first run to next to the script, the real script_dir()
    (wherever pytest itself was launched from)."""
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "home-config" / "config.ini")
    monkeypatch.setattr("sys.argv", [str(tmp_path / "script-dir" / "sidecut.py")])


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
