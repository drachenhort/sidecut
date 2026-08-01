from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

import lidarr


def test_check_connection_returns_version() -> None:
    response = Mock(status_code=200)
    response.json.return_value = {"version": "1.2.3"}

    with patch("requests.get", return_value=response) as get:
        version = lidarr.check_connection("http://localhost:8686", "key")

    assert version == "1.2.3"
    assert get.call_args.kwargs["headers"] == {"X-Api-Key": "key"}


def test_check_connection_raises_on_bad_api_key() -> None:
    response = Mock(status_code=401)

    with patch("requests.get", return_value=response):
        with pytest.raises(lidarr.LidarrError, match="rejected the API key"):
            lidarr.check_connection("http://localhost:8686", "wrong-key")


def test_check_connection_raises_when_unreachable() -> None:
    with patch("requests.get", side_effect=requests.ConnectionError("no route")):
        with pytest.raises(lidarr.LidarrError, match="Could not reach Lidarr"):
            lidarr.check_connection("http://localhost:8686", "key")


def test_get_manual_import_candidates_passes_folder_and_returns_json() -> None:
    response = Mock()
    response.json.return_value = [{"path": "/music/song.mp3"}]

    with patch("requests.get", return_value=response) as get:
        candidates = lidarr.get_manual_import_candidates("http://localhost:8686", "key", Path("/music"))

    assert candidates == [{"path": "/music/song.mp3"}]
    assert get.call_args.kwargs["params"]["folder"] == "/music"


def test_is_fully_matched() -> None:
    matched = {"artist": {"id": 1}, "album": {"id": 2}, "tracks": [{"id": 3}], "rejections": []}
    assert lidarr.is_fully_matched(matched) is True

    assert lidarr.is_fully_matched({**matched, "artist": None}) is False
    assert lidarr.is_fully_matched({**matched, "tracks": []}) is False
    assert lidarr.is_fully_matched({**matched, "rejections": ["no album found"]}) is False


def test_submit_manual_import_posts_command_and_returns_id() -> None:
    response = Mock()
    response.json.return_value = {"id": 42}

    with patch("requests.post", return_value=response) as post:
        command_id = lidarr.submit_manual_import("http://localhost:8686", "key", [{"path": "a"}])

    assert command_id == 42
    payload = post.call_args.kwargs["json"]
    assert payload["name"] == "ManualImport"
    assert payload["files"] == [{"path": "a"}]
    assert payload["importMode"] == "move"


def test_wait_for_command_returns_once_completed() -> None:
    responses = [Mock(), Mock()]
    responses[0].json.return_value = {"status": "started"}
    responses[1].json.return_value = {"status": "completed"}

    with patch("requests.get", side_effect=responses), patch("time.sleep") as sleep:
        result = lidarr.wait_for_command("http://localhost:8686", "key", 42)

    assert result == {"status": "completed"}
    assert sleep.called


def test_wait_for_command_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    response = Mock()
    response.json.return_value = {"status": "started"}

    times = iter([0.0, 0.0, 100.0])
    monkeypatch.setattr(lidarr.time, "monotonic", lambda: next(times))

    with patch("requests.get", return_value=response), patch("time.sleep"):
        with pytest.raises(lidarr.LidarrError, match="Timed out"):
            lidarr.wait_for_command("http://localhost:8686", "key", 42, timeout=1.0)


def test_import_folder_only_submits_matched_candidates() -> None:
    candidates = [
        {"path": "/music/a.mp3", "artist": {"id": 1}, "album": {"id": 2}, "tracks": [{"id": 3}], "rejections": []},
        {"path": "/music/b.mp3", "artist": None, "album": None, "tracks": [], "rejections": ["no match"]},
    ]
    get_response = Mock()
    get_response.json.return_value = candidates
    post_response = Mock()
    post_response.json.return_value = {"id": 1}
    command_response = Mock()
    command_response.json.return_value = {"status": "completed"}

    with (
        patch("requests.get", side_effect=[get_response, command_response]),
        patch("requests.post", return_value=post_response) as post,
    ):
        imported, skipped, skipped_names = lidarr.import_folder("http://localhost:8686", "key", Path("/music"))

    assert imported == 1
    assert skipped == 1
    assert skipped_names == ["b.mp3"]
    assert len(post.call_args.kwargs["json"]["files"]) == 1


def test_import_folder_skips_submit_when_nothing_matched() -> None:
    get_response = Mock()
    get_response.json.return_value = [
        {"path": "/music/b.mp3", "artist": None, "album": None, "tracks": [], "rejections": ["no match"]},
    ]

    with patch("requests.get", return_value=get_response), patch("requests.post") as post:
        imported, skipped, skipped_names = lidarr.import_folder("http://localhost:8686", "key", Path("/music"))

    assert imported == 0
    assert skipped == 1
    assert skipped_names == ["b.mp3"]
    post.assert_not_called()


def test_import_folder_raises_on_lidarr_reported_failure() -> None:
    candidates = [
        {"path": "/music/a.mp3", "artist": {"id": 1}, "album": {"id": 2}, "tracks": [{"id": 3}], "rejections": []},
    ]
    get_response = Mock()
    get_response.json.return_value = candidates
    post_response = Mock()
    post_response.json.return_value = {"id": 1}
    command_response = Mock()
    command_response.json.return_value = {"status": "failed", "message": "disk full"}

    with (
        patch("requests.get", side_effect=[get_response, command_response]),
        patch("requests.post", return_value=post_response),
    ):
        with pytest.raises(lidarr.LidarrError, match="disk full"):
            lidarr.import_folder("http://localhost:8686", "key", Path("/music"))
