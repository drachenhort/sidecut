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


def test_skip_reason_prefers_lidarr_rejection_text() -> None:
    item = {"artist": None, "album": None, "tracks": [], "rejections": [{"reason": "Track already has file"}]}
    assert lidarr.skip_reason(item) == "Track already has file"


def test_skip_reason_falls_back_to_which_field_is_missing() -> None:
    assert lidarr.skip_reason({"artist": None, "album": None, "tracks": []}) == "no artist match"
    assert lidarr.skip_reason({"artist": {"id": 1}, "album": None, "tracks": []}) == "no album match"
    assert lidarr.skip_reason({"artist": {"id": 1}, "album": {"id": 2}, "tracks": []}) == "no track match"


def test_trigger_rescan_posts_rescan_command() -> None:
    response = Mock()
    response.json.return_value = {"id": 7}

    with patch("requests.post", return_value=response) as post:
        command_id = lidarr.trigger_rescan("http://localhost:8686", "key")

    assert command_id == 7
    assert post.call_args.kwargs["json"] == {"name": "RescanFolders"}


def test_import_folder_only_submits_matched_candidates() -> None:
    candidates = [
        {"path": "/music/a.mp3", "artist": {"id": 1}, "album": {"id": 2}, "tracks": [{"id": 3}], "rejections": []},
        {
            "path": "/music/b.mp3",
            "artist": None,
            "album": None,
            "tracks": [],
            "rejections": [{"reason": "Track already has file"}],
        },
    ]
    rescan_wait_response = Mock()
    rescan_wait_response.json.return_value = {"status": "completed"}
    candidates_response = Mock()
    candidates_response.json.return_value = candidates
    import_wait_response = Mock()
    import_wait_response.json.return_value = {"status": "completed"}
    rescan_post_response = Mock()
    rescan_post_response.json.return_value = {"id": 1}
    import_post_response = Mock()
    import_post_response.json.return_value = {"id": 2}

    with (
        patch("requests.get", side_effect=[rescan_wait_response, candidates_response, import_wait_response]),
        patch("requests.post", side_effect=[rescan_post_response, import_post_response]) as post,
    ):
        imported, skipped, skipped_names = lidarr.import_folder("http://localhost:8686", "key", Path("/music"))

    assert imported == 1
    assert skipped == 1
    assert skipped_names == ["b.mp3: Track already has file"]
    import_payload = post.call_args_list[1].kwargs["json"]
    assert len(import_payload["files"]) == 1


def test_import_folder_skips_submit_when_nothing_matched() -> None:
    rescan_wait_response = Mock()
    rescan_wait_response.json.return_value = {"status": "completed"}
    candidates_response = Mock()
    candidates_response.json.return_value = [
        {"path": "/music/b.mp3", "artist": None, "album": None, "tracks": [], "rejections": ["no match"]},
    ]
    rescan_post_response = Mock()
    rescan_post_response.json.return_value = {"id": 1}

    with (
        patch("requests.get", side_effect=[rescan_wait_response, candidates_response]),
        patch("requests.post", return_value=rescan_post_response) as post,
    ):
        imported, skipped, skipped_names = lidarr.import_folder("http://localhost:8686", "key", Path("/music"))

    assert imported == 0
    assert skipped == 1
    assert skipped_names == ["b.mp3: no match"]
    post.assert_called_once()  # only the rescan trigger, no ManualImport submitted


def test_import_folder_raises_on_lidarr_reported_failure() -> None:
    candidates = [
        {"path": "/music/a.mp3", "artist": {"id": 1}, "album": {"id": 2}, "tracks": [{"id": 3}], "rejections": []},
    ]
    rescan_wait_response = Mock()
    rescan_wait_response.json.return_value = {"status": "completed"}
    candidates_response = Mock()
    candidates_response.json.return_value = candidates
    import_wait_response = Mock()
    import_wait_response.json.return_value = {"status": "failed", "message": "disk full"}
    rescan_post_response = Mock()
    rescan_post_response.json.return_value = {"id": 1}
    import_post_response = Mock()
    import_post_response.json.return_value = {"id": 2}

    with (
        patch("requests.get", side_effect=[rescan_wait_response, candidates_response, import_wait_response]),
        patch("requests.post", side_effect=[rescan_post_response, import_post_response]),
    ):
        with pytest.raises(lidarr.LidarrError, match="disk full"):
            lidarr.import_folder("http://localhost:8686", "key", Path("/music"))


def test_import_folder_proceeds_when_rescan_trigger_fails() -> None:
    # The pre-import rescan is best-effort: if Lidarr rejects/doesn't
    # support it, the import itself must still go ahead rather than failing.
    candidates = [
        {"path": "/music/a.mp3", "artist": {"id": 1}, "album": {"id": 2}, "tracks": [{"id": 3}], "rejections": []},
    ]
    candidates_response = Mock()
    candidates_response.json.return_value = candidates
    import_wait_response = Mock()
    import_wait_response.json.return_value = {"status": "completed"}
    import_post_response = Mock()
    import_post_response.json.return_value = {"id": 2}

    with (
        patch("requests.get", side_effect=[candidates_response, import_wait_response]),
        patch(
            "requests.post",
            side_effect=[requests.ConnectionError("unknown command"), import_post_response],
        ),
    ):
        imported, skipped, skipped_names = lidarr.import_folder("http://localhost:8686", "key", Path("/music"))

    assert imported == 1
    assert skipped == 0
