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


def test_remap_path_to_lidarr_rewrites_matching_prefix() -> None:
    result = lidarr.remap_path_to_lidarr(Path("/home/user/Music/Artist/Album"), "/home/user/Music", "/music")
    assert result == "/music/Artist/Album"


def test_remap_path_to_lidarr_leaves_path_unchanged_when_roots_blank() -> None:
    path = Path("/home/user/Music/Artist/Album")
    assert lidarr.remap_path_to_lidarr(path, "", "/music") == str(path)
    assert lidarr.remap_path_to_lidarr(path, "/home/user/Music", "") == str(path)


def test_remap_path_to_lidarr_leaves_path_unchanged_when_not_under_local_root() -> None:
    path = Path("/somewhere/else/Artist")
    assert lidarr.remap_path_to_lidarr(path, "/home/user/Music", "/music") == str(path)


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
    assert payload["importMode"] == "auto"
    assert payload["replaceExistingFiles"] is False


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


def test_has_existing_file_rejection() -> None:
    blocked = {"album": {"id": 5}, "rejections": [{"reason": "Track already has file"}]}
    assert lidarr.has_existing_file_rejection(blocked) is True

    assert lidarr.has_existing_file_rejection({"album": None, "rejections": [{"reason": "Track already has file"}]}) is False
    assert lidarr.has_existing_file_rejection({"album": {"id": 5}, "rejections": [{"reason": "no audio files"}]}) is False
    assert lidarr.has_existing_file_rejection({"album": {"id": 5}, "rejections": []}) is False


def test_get_album_trackfiles_passes_album_id() -> None:
    response = Mock()
    response.json.return_value = [{"id": 1}, {"id": 2}]

    with patch("requests.get", return_value=response) as get:
        trackfiles = lidarr.get_album_trackfiles("http://localhost:8686", "key", 5)

    assert trackfiles == [{"id": 1}, {"id": 2}]
    assert get.call_args.kwargs["params"] == {"albumId": 5}


def test_clear_stale_trackfiles_deletes_each_one() -> None:
    list_response = Mock()
    list_response.json.return_value = [{"id": 1}, {"id": 2}]

    with (
        patch("requests.get", return_value=list_response),
        patch("requests.delete", return_value=Mock()) as delete,
    ):
        count = lidarr.clear_stale_trackfiles("http://localhost:8686", "key", 5)

    assert count == 2
    deleted_ids = sorted(call.args[0].rsplit("/", 1)[-1] for call in delete.call_args_list)
    assert deleted_ids == ["1", "2"]


def test_import_folder_remaps_folder_to_lidarrs_path_before_scanning() -> None:
    candidates_response = Mock()
    candidates_response.json.return_value = []

    with patch("requests.get", return_value=candidates_response) as get:
        lidarr.import_folder(
            "http://localhost:8686",
            "key",
            Path("/home/user/Music/Artist"),
            local_root="/home/user/Music",
            lidarr_root="/music",
        )

    assert get.call_args.kwargs["params"]["folder"] == "/music/Artist"


def test_import_folder_only_submits_matched_candidates() -> None:
    candidates = [
        {"path": "/music/a.mp3", "artist": {"id": 1}, "album": {"id": 2}, "tracks": [{"id": 3}], "rejections": []},
        {
            "path": "/music/b.mp3",
            "artist": None,
            "album": None,
            "tracks": [],
            "rejections": [{"reason": "no match"}],
        },
    ]
    candidates_response = Mock()
    candidates_response.json.return_value = candidates
    import_wait_response = Mock()
    import_wait_response.json.return_value = {"status": "completed"}
    import_post_response = Mock()
    import_post_response.json.return_value = {"id": 2}

    with (
        patch("requests.get", side_effect=[candidates_response, import_wait_response]),
        patch("requests.post", return_value=import_post_response) as post,
    ):
        imported, skipped, skipped_names = lidarr.import_folder("http://localhost:8686", "key", Path("/music"))

    assert imported == 1
    assert skipped == 1
    assert skipped_names == ["b.mp3: no match"]
    import_payload = post.call_args.kwargs["json"]
    assert len(import_payload["files"]) == 1


def test_import_folder_skips_submit_when_nothing_matched() -> None:
    candidates_response = Mock()
    candidates_response.json.return_value = [
        {"path": "/music/b.mp3", "artist": None, "album": None, "tracks": [], "rejections": ["no match"]},
    ]

    with patch("requests.get", return_value=candidates_response), patch("requests.post") as post:
        imported, skipped, skipped_names = lidarr.import_folder("http://localhost:8686", "key", Path("/music"))

    assert imported == 0
    assert skipped == 1
    assert skipped_names == ["b.mp3: no match"]
    post.assert_not_called()


def test_import_folder_clears_stale_trackfile_and_retries() -> None:
    # First scan: Lidarr rejects the MP3 because it still has a TrackFile
    # record for the FLAC this tool converted and deleted.
    first_scan = [
        {
            "path": "/music/a.mp3",
            "artist": {"id": 1},
            "album": {"id": 5},
            "tracks": [],
            "rejections": [{"reason": "Track already has file"}],
        },
    ]
    # After clearing the stale record, the retry scan matches cleanly.
    second_scan = [
        {"path": "/music/a.mp3", "artist": {"id": 1}, "album": {"id": 5}, "tracks": [{"id": 3}], "rejections": []},
    ]
    first_scan_response = Mock()
    first_scan_response.json.return_value = first_scan
    trackfiles_response = Mock()
    trackfiles_response.json.return_value = [{"id": 99}]
    second_scan_response = Mock()
    second_scan_response.json.return_value = second_scan
    import_wait_response = Mock()
    import_wait_response.json.return_value = {"status": "completed"}
    import_post_response = Mock()
    import_post_response.json.return_value = {"id": 2}

    with (
        patch(
            "requests.get",
            side_effect=[first_scan_response, trackfiles_response, second_scan_response, import_wait_response],
        ),
        patch("requests.delete", return_value=Mock()) as delete,
        patch("requests.post", return_value=import_post_response),
    ):
        imported, skipped, skipped_names = lidarr.import_folder("http://localhost:8686", "key", Path("/music"))

    assert imported == 1
    assert skipped == 0
    assert skipped_names == []
    delete.assert_called_once()


def test_import_folder_raises_on_lidarr_reported_failure() -> None:
    candidates = [
        {"path": "/music/a.mp3", "artist": {"id": 1}, "album": {"id": 2}, "tracks": [{"id": 3}], "rejections": []},
    ]
    candidates_response = Mock()
    candidates_response.json.return_value = candidates
    import_wait_response = Mock()
    import_wait_response.json.return_value = {"status": "failed", "message": "disk full"}
    import_post_response = Mock()
    import_post_response.json.return_value = {"id": 2}

    with (
        patch("requests.get", side_effect=[candidates_response, import_wait_response]),
        patch("requests.post", return_value=import_post_response),
    ):
        with pytest.raises(lidarr.LidarrError, match="disk full"):
            lidarr.import_folder("http://localhost:8686", "key", Path("/music"))
