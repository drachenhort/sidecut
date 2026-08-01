from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

import lidarr


def _no_artist_match_response() -> Mock:
    """Mocks the GET /api/v1/artist call import_folder() makes up front to
    resolve an artistId by path; an empty list means "no match found",
    same as today's behavior when that lookup isn't relevant to a test."""
    response = Mock()
    response.json.return_value = []
    return response


def _matched_candidate(path: str, artist_id: int = 1, album_id: int = 2, track_id: int = 3) -> dict:
    return {
        "path": path,
        "artist": {"id": artist_id},
        "album": {"id": album_id},
        "albumReleaseId": 99,
        "tracks": [{"id": track_id}],
        "quality": {"quality": {"id": 4, "name": "MP3-320"}},
        "rejections": [],
    }


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
    assert get.call_args.kwargs["timeout"] == lidarr.MANUAL_IMPORT_SCAN_TIMEOUT
    assert "artistId" not in get.call_args.kwargs["params"]


def test_get_manual_import_candidates_includes_artist_id_when_given() -> None:
    response = Mock()
    response.json.return_value = []

    with patch("requests.get", return_value=response) as get:
        lidarr.get_manual_import_candidates("http://localhost:8686", "key", Path("/music"), artist_id=855)

    assert get.call_args.kwargs["params"]["artistId"] == 855


def test_get_artist_id_for_path_matches_exact_path() -> None:
    response = Mock()
    response.json.return_value = [
        {"id": 434, "path": "/music/Jelly Roll"},
        {"id": 967, "path": "/music/Jelly Roll (Rock band from Georgia)"},
    ]

    with patch("requests.get", return_value=response):
        assert lidarr.get_artist_id_for_path("http://localhost:8686", "key", "/music/Jelly Roll") == 434


def test_get_artist_id_for_path_matches_subfolder() -> None:
    response = Mock()
    response.json.return_value = [{"id": 855, "path": "/music/Alex Anwandter"}]

    with patch("requests.get", return_value=response):
        result = lidarr.get_artist_id_for_path(
            "http://localhost:8686", "key", "/music/Alex Anwandter/Amiga (2016)"
        )

    assert result == 855


def test_get_artist_id_for_path_returns_none_without_a_match() -> None:
    response = Mock()
    response.json.return_value = [{"id": 1, "path": "/music/Someone Else"}]

    with patch("requests.get", return_value=response):
        assert lidarr.get_artist_id_for_path("http://localhost:8686", "key", "/music/Jelly Roll") is None


def test_get_artist_id_for_path_does_not_false_match_a_similarly_named_prefix() -> None:
    # "/music/Jelly Roll" must not match an artist at "/music/Jelly Rollers"
    response = Mock()
    response.json.return_value = [{"id": 1, "path": "/music/Jelly Rollers"}]

    with patch("requests.get", return_value=response):
        assert lidarr.get_artist_id_for_path("http://localhost:8686", "key", "/music/Jelly Roll") is None


def test_import_folder_resolves_and_passes_artist_id() -> None:
    artist_response = Mock()
    artist_response.json.return_value = [{"id": 434, "path": "/music/Jelly Roll"}]
    artist_trackfiles_response = Mock()
    artist_trackfiles_response.json.return_value = []  # nothing stale to clear
    candidates_response = Mock()
    candidates_response.json.return_value = []

    with patch(
        "requests.get", side_effect=[artist_response, artist_trackfiles_response, candidates_response]
    ) as get:
        lidarr.import_folder("http://localhost:8686", "key", Path("/music/Jelly Roll"))

    assert get.call_args_list[2].kwargs["params"]["artistId"] == 434


def test_import_folder_clears_artist_stale_trackfiles_before_scanning_to_avoid_scan_crash(
    tmp_path: Path,
) -> None:
    # Even one stale trackfile can crash Lidarr's manual-import scan
    # entirely (a 500 from its AugmentingService trying to read the
    # missing file) rather than a clean per-file rejection - so stale
    # records for the resolved artist are cleared proactively, before the
    # scan runs at all, not just reactively after seeing a rejection.
    (tmp_path / "still-here.mp3").write_bytes(b"data")

    artist_response = Mock()
    artist_response.json.return_value = [{"id": 434, "path": "/music/Jelly Roll"}]
    artist_trackfiles_response = Mock()
    artist_trackfiles_response.json.return_value = [
        {"id": 1, "path": "/music/Jelly Roll/gone.flac"},  # stale: file doesn't exist
        {"id": 2, "path": "/music/Jelly Roll/still-here.mp3"},  # not stale: exists
    ]
    candidates_response = Mock()
    candidates_response.json.return_value = []

    with (
        patch(
            "requests.get", side_effect=[artist_response, artist_trackfiles_response, candidates_response]
        ),
        patch("requests.delete", return_value=Mock()) as delete,
    ):
        lidarr.import_folder(
            "http://localhost:8686",
            "key",
            Path("/music/Jelly Roll"),
            local_root=str(tmp_path),
            lidarr_root="/music/Jelly Roll",
        )

    deleted_ids = [call.args[0].rsplit("/", 1)[-1] for call in delete.call_args_list]
    assert deleted_ids == ["1"]


def test_get_manual_import_candidates_raises_clear_error_on_timeout() -> None:
    with patch("requests.get", side_effect=requests.Timeout("timed out")):
        with pytest.raises(lidarr.LidarrError, match="timed out"):
            lidarr.get_manual_import_candidates("http://localhost:8686", "key", Path("/music"))


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
        command_id = lidarr.submit_manual_import("http://localhost:8686", "key", [_matched_candidate("/music/a.mp3")])

    assert command_id == 42
    payload = post.call_args.kwargs["json"]
    assert payload["name"] == "ManualImport"
    assert payload["importMode"] == "auto"
    assert payload["replaceExistingFiles"] is False


def test_submit_manual_import_flattens_nested_ids_for_the_command() -> None:
    # GET /api/v1/manualimport nests artist/album/tracks as full objects;
    # POST /api/v1/command's ManualImport needs flat artistId/albumId/
    # trackIds - sending the raw GET shape silently sends artistId=0/
    # albumId=0 and the command gets stuck at "queued" forever.
    response = Mock()
    response.json.return_value = {"id": 42}
    candidate = _matched_candidate("/music/a.mp3", artist_id=855, album_id=10098, track_id=811503)

    with patch("requests.post", return_value=response) as post:
        lidarr.submit_manual_import("http://localhost:8686", "key", [candidate])

    files = post.call_args.kwargs["json"]["files"]
    assert files == [
        {
            "path": "/music/a.mp3",
            "artistId": 855,
            "albumId": 10098,
            "albumReleaseId": 99,
            "trackIds": [811503],
            "quality": {"quality": {"id": 4, "name": "MP3-320"}},
            "indexerFlags": 0,
            "disableReleaseSwitching": False,
        }
    ]


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


def test_wait_for_command_does_not_time_out_while_merely_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    # Being queued behind other, unrelated Lidarr work (e.g. a large
    # library rescan) is normal - it must not trip the tight "started"
    # timeout just because a lot of wall-clock time passes while queued.
    responses = [Mock(), Mock(), Mock()]
    responses[0].json.return_value = {"status": "queued"}
    responses[1].json.return_value = {"status": "queued"}
    responses[2].json.return_value = {"status": "completed"}

    # Simulate a long time passing while queued (longer than `timeout`,
    # the started-phase budget) without ever reporting "started".
    times = iter([0.0, 0.0, 500.0])
    monkeypatch.setattr(lidarr.time, "monotonic", lambda: next(times))

    with patch("requests.get", side_effect=responses), patch("time.sleep"):
        result = lidarr.wait_for_command("http://localhost:8686", "key", 42, timeout=1.0, queue_timeout=10_000.0)

    assert result == {"status": "completed"}


def test_wait_for_command_times_out_while_queued_too_long() -> None:
    response = Mock()
    response.json.return_value = {"status": "queued"}

    with patch("requests.get", return_value=response), patch("time.sleep"):
        with pytest.raises(lidarr.LidarrError, match="still queued"):
            lidarr.wait_for_command("http://localhost:8686", "key", 42, queue_timeout=0.01)


def test_skip_reason_prefers_lidarr_rejection_text() -> None:
    item = {"artist": None, "album": None, "tracks": [], "rejections": [{"reason": "Track already has file"}]}
    assert lidarr.skip_reason(item) == "Track already has file"


def test_skip_reason_falls_back_to_which_field_is_missing() -> None:
    assert lidarr.skip_reason({"artist": None, "album": None, "tracks": []}) == "no artist match"
    assert lidarr.skip_reason({"artist": {"id": 1}, "album": None, "tracks": []}) == "no album match"
    assert lidarr.skip_reason({"artist": {"id": 1}, "album": {"id": 2}, "tracks": []}) == "no track match"


def test_has_missing_album_rejection() -> None:
    item = {"rejections": [{"reason": "Couldn't find similar album for [/music/Artist/Album (2011)]"}]}
    assert lidarr.has_missing_album_rejection(item) is True
    assert lidarr.has_missing_album_rejection({"rejections": [{"reason": "Track already has file"}]}) is False
    assert lidarr.has_missing_album_rejection({"rejections": []}) is False


def test_get_metadata_profile_disallowed_types() -> None:
    response = Mock()
    response.json.return_value = [
        {
            "id": 1,
            "primaryAlbumTypes": [
                {"albumType": {"name": "Album"}, "allowed": True},
                {"albumType": {"name": "Single"}, "allowed": False},
            ],
            "secondaryAlbumTypes": [
                {"albumType": {"name": "Studio"}, "allowed": True},
                {"albumType": {"name": "Compilation"}, "allowed": False},
                {"albumType": {"name": "Live"}, "allowed": False},
            ],
        }
    ]

    with patch("requests.get", return_value=response):
        result = lidarr.get_metadata_profile_disallowed_types("http://localhost:8686", "key", 1)

    assert result == {"Single", "Compilation", "Live"}


def test_get_metadata_profile_disallowed_types_no_matching_profile() -> None:
    response = Mock()
    response.json.return_value = [{"id": 2, "secondaryAlbumTypes": []}]

    with patch("requests.get", return_value=response):
        result = lidarr.get_metadata_profile_disallowed_types("http://localhost:8686", "key", 1)

    assert result == set()


def test_explain_missing_album_finds_a_blocked_compilation() -> None:
    artist_response = Mock()
    artist_response.json.return_value = {
        "artistName": "Eisbrecher",
        "foreignArtistId": "36e348fe-0cbc-4343-8fa5-b91e727ce94f",
        "metadataProfileId": 1,
    }
    lookup_response = Mock()
    lookup_response.json.return_value = [
        {
            "title": "Eiskalt",
            "secondaryTypes": ["Compilation"],
            "artist": {"foreignArtistId": "36e348fe-0cbc-4343-8fa5-b91e727ce94f"},
        }
    ]
    profile_response = Mock()
    profile_response.json.return_value = [
        {
            "id": 1,
            "secondaryAlbumTypes": [{"albumType": {"name": "Compilation"}, "allowed": False}],
        }
    ]

    with patch("requests.get", side_effect=[artist_response, lookup_response, profile_response]):
        result = lidarr.explain_missing_album("http://localhost:8686", "key", 333, "Eiskalt (2011)")

    assert result is not None
    assert "Compilation" in result
    assert "Eiskalt" in result
    assert "Eisbrecher" in result


def test_explain_missing_album_returns_none_when_type_is_allowed() -> None:
    artist_response = Mock()
    artist_response.json.return_value = {
        "artistName": "Eisbrecher",
        "foreignArtistId": "36e348fe-0cbc-4343-8fa5-b91e727ce94f",
        "metadataProfileId": 1,
    }
    lookup_response = Mock()
    lookup_response.json.return_value = [
        {
            "title": "Eiskalt",
            "secondaryTypes": ["Compilation"],
            "artist": {"foreignArtistId": "36e348fe-0cbc-4343-8fa5-b91e727ce94f"},
        }
    ]
    profile_response = Mock()
    profile_response.json.return_value = [
        {"id": 1, "secondaryAlbumTypes": [{"albumType": {"name": "Compilation"}, "allowed": True}]},
    ]

    with patch("requests.get", side_effect=[artist_response, lookup_response, profile_response]):
        result = lidarr.explain_missing_album("http://localhost:8686", "key", 333, "Eiskalt (2011)")

    assert result is None


def test_explain_missing_album_finds_a_blocked_primary_type() -> None:
    # Lidarr filters on primary album type (Album/EP/Single/Broadcast/
    # Other) independently of secondary type - a Single can be excluded
    # even with no Compilation/Live/etc. secondary type involved at all.
    artist_response = Mock()
    artist_response.json.return_value = {
        "artistName": "Eisbrecher",
        "foreignArtistId": "36e348fe-0cbc-4343-8fa5-b91e727ce94f",
        "metadataProfileId": 1,
    }
    lookup_response = Mock()
    lookup_response.json.return_value = [
        {
            "title": "10 Jahre Eisbrecher",
            "albumType": "Single",
            "secondaryTypes": [],
            "artist": {"foreignArtistId": "36e348fe-0cbc-4343-8fa5-b91e727ce94f"},
        }
    ]
    profile_response = Mock()
    profile_response.json.return_value = [
        {"id": 1, "primaryAlbumTypes": [{"albumType": {"name": "Single"}, "allowed": False}]},
    ]

    with patch("requests.get", side_effect=[artist_response, lookup_response, profile_response]):
        result = lidarr.explain_missing_album("http://localhost:8686", "key", 333, "10 Jahre Eisbrecher")

    assert result is not None
    assert "Single" in result


def test_explain_missing_album_returns_none_when_no_title_match() -> None:
    artist_response = Mock()
    artist_response.json.return_value = {
        "artistName": "Eisbrecher",
        "foreignArtistId": "36e348fe-0cbc-4343-8fa5-b91e727ce94f",
        "metadataProfileId": 1,
    }
    lookup_response = Mock()
    lookup_response.json.return_value = [
        {"title": "Something Else Entirely", "secondaryTypes": [], "artist": {"foreignArtistId": "x"}}
    ]

    with patch("requests.get", side_effect=[artist_response, lookup_response]):
        result = lidarr.explain_missing_album("http://localhost:8686", "key", 333, "Eiskalt (2011)")

    assert result is None


def test_explain_missing_album_returns_none_on_request_failure() -> None:
    with patch("requests.get", side_effect=requests.ConnectionError("no route")):
        result = lidarr.explain_missing_album("http://localhost:8686", "key", 333, "Eiskalt (2011)")

    assert result is None


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


def test_lidarr_path_to_local_rewrites_matching_prefix() -> None:
    result = lidarr.lidarr_path_to_local("/music/Artist/song.mp3", "/home/user/Music", "/music")
    assert result == Path("/home/user/Music/Artist/song.mp3")


def test_lidarr_path_to_local_leaves_path_unchanged_when_roots_blank() -> None:
    assert lidarr.lidarr_path_to_local("/music/Artist/song.mp3", "", "/music") == Path("/music/Artist/song.mp3")
    assert lidarr.lidarr_path_to_local("/music/Artist/song.mp3", "/home/user/Music", "") == Path(
        "/music/Artist/song.mp3"
    )


def test_clear_stale_trackfiles_only_deletes_records_whose_file_is_actually_gone(tmp_path: Path) -> None:
    # This is the real-world safety net: DELETE /api/v1/trackfile removes
    # the actual file, so a record must never be deleted just because it
    # belongs to the album - only when its file is confirmed missing.
    still_there = tmp_path / "02 - still there.mp3"
    still_there.write_bytes(b"data")
    # "01 - gone.flac" deliberately not created: this tool already deleted
    # it during conversion, so the trackfile record for it is genuinely stale.

    list_response = Mock()
    list_response.json.return_value = [
        {"id": 1, "path": str(tmp_path / "01 - gone.flac")},
        {"id": 2, "path": str(still_there)},
    ]

    with (
        patch("requests.get", return_value=list_response),
        patch("requests.delete", return_value=Mock()) as delete,
    ):
        count = lidarr.clear_stale_trackfiles("http://localhost:8686", "key", 5)

    assert count == 1
    deleted_ids = [call.args[0].rsplit("/", 1)[-1] for call in delete.call_args_list]
    assert deleted_ids == ["1"]
    assert still_there.exists()  # never touched


def test_clear_stale_trackfiles_paces_out_multiple_deletes(tmp_path: Path) -> None:
    # Many stale records (e.g. clearing a whole discography at once)
    # shouldn't fire a burst of DELETE calls back-to-back.
    list_response = Mock()
    list_response.json.return_value = [
        {"id": 1, "path": str(tmp_path / "01.flac")},
        {"id": 2, "path": str(tmp_path / "02.flac")},
        {"id": 3, "path": str(tmp_path / "03.flac")},
    ]

    with (
        patch("requests.get", return_value=list_response),
        patch("requests.delete", return_value=Mock()),
        patch("time.sleep") as sleep,
    ):
        count = lidarr.clear_stale_trackfiles("http://localhost:8686", "key", 5)

    assert count == 3
    assert sleep.call_count == 2  # a pause between each of the 3 deletes, not before the first


def test_clear_stale_trackfiles_skips_when_parent_directory_is_unreachable(tmp_path: Path) -> None:
    # If we can't even see the containing folder, this machine likely
    # isn't looking at the same filesystem Lidarr is (e.g. a cross-host
    # setup with local_root/lidarr_root not configured). Guessing "stale"
    # in that situation is exactly what caused real data loss - so this
    # must leave the record alone rather than delete it.
    list_response = Mock()
    list_response.json.return_value = [{"id": 1, "path": "/totally/unrelated/namespace/song.mp3"}]

    with (
        patch("requests.get", return_value=list_response),
        patch("requests.delete", return_value=Mock()) as delete,
    ):
        count = lidarr.clear_stale_trackfiles("http://localhost:8686", "key", 5)

    assert count == 0
    delete.assert_not_called()


def test_clear_stale_trackfiles_uses_path_mapping_when_configured(tmp_path: Path) -> None:
    still_there = tmp_path / "Artist" / "song.mp3"
    still_there.parent.mkdir()
    still_there.write_bytes(b"data")

    list_response = Mock()
    list_response.json.return_value = [{"id": 1, "path": "/music/Artist/song.mp3"}]

    with (
        patch("requests.get", return_value=list_response),
        patch("requests.delete", return_value=Mock()) as delete,
    ):
        count = lidarr.clear_stale_trackfiles(
            "http://localhost:8686", "key", 5, local_root=str(tmp_path), lidarr_root="/music"
        )

    assert count == 0
    delete.assert_not_called()


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


def test_import_folder_submits_large_batches_in_chunks() -> None:
    # 45 matched files with IMPORT_BATCH_SIZE=20 should become 3 separate
    # ManualImport commands (20, 20, 5) rather than one command for all
    # 45 at once - a single huge command is what was overwhelming a real
    # Lidarr instance.
    candidates = [_matched_candidate(f"/music/{i:03d}.mp3", track_id=i) for i in range(45)]
    candidates_response = Mock()
    candidates_response.json.return_value = candidates

    post_response = Mock()
    post_response.json.return_value = {"id": 1}
    wait_response = Mock()
    wait_response.json.return_value = {"status": "completed"}

    with (
        patch(
            "requests.get",
            side_effect=[_no_artist_match_response(), candidates_response, wait_response, wait_response, wait_response],
        ),
        patch("requests.post", return_value=post_response) as post,
        patch("time.sleep") as sleep,
    ):
        imported, skipped, skipped_names = lidarr.import_folder("http://localhost:8686", "key", Path("/music"))

    assert imported == 45
    assert skipped == 0
    assert [len(call.kwargs["json"]["files"]) for call in post.call_args_list] == [20, 20, 5]
    assert sleep.call_count == 2  # a pause between each of the 3 batches, not before the first


def test_import_folder_only_submits_matched_candidates() -> None:
    candidates = [
        _matched_candidate("/music/a.mp3"),
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
        patch(
            "requests.get", side_effect=[_no_artist_match_response(), candidates_response, import_wait_response]
        ),
        patch("requests.post", return_value=import_post_response) as post,
    ):
        imported, skipped, skipped_names = lidarr.import_folder("http://localhost:8686", "key", Path("/music"))

    assert imported == 1
    assert skipped == 1
    assert skipped_names == ["b.mp3: no match"]
    import_payload = post.call_args.kwargs["json"]
    assert import_payload["files"] == [
        {
            "path": "/music/a.mp3",
            "artistId": 1,
            "albumId": 2,
            "albumReleaseId": 99,
            "trackIds": [3],
            "quality": {"quality": {"id": 4, "name": "MP3-320"}},
            "indexerFlags": 0,
            "disableReleaseSwitching": False,
        }
    ]


def test_import_folder_enriches_missing_album_skips_and_caches_per_folder() -> None:
    artist_response = Mock()
    artist_response.json.return_value = [{"id": 855, "path": "/music/Eisbrecher"}]
    artist_trackfiles_response = Mock()
    artist_trackfiles_response.json.return_value = []
    candidates = [
        {
            "path": "/music/Eisbrecher/Eiskalt (2011)/01.mp3",
            "artist": None,
            "album": None,
            "tracks": [],
            "rejections": [{"reason": "Couldn't find similar album for [/music/Eisbrecher/Eiskalt (2011)]"}],
        },
        {
            "path": "/music/Eisbrecher/Eiskalt (2011)/02.mp3",
            "artist": None,
            "album": None,
            "tracks": [],
            "rejections": [{"reason": "Couldn't find similar album for [/music/Eisbrecher/Eiskalt (2011)]"}],
        },
    ]
    candidates_response = Mock()
    candidates_response.json.return_value = candidates

    with (
        patch(
            "requests.get", side_effect=[artist_response, artist_trackfiles_response, candidates_response]
        ),
        patch("lidarr.explain_missing_album", return_value="explained: it's a Compilation") as explain,
    ):
        imported, skipped, skipped_names = lidarr.import_folder(
            "http://localhost:8686", "key", Path("/music/Eisbrecher/Eiskalt (2011)")
        )

    assert imported == 0
    assert skipped == 2
    assert skipped_names == ["01.mp3: explained: it's a Compilation", "02.mp3: explained: it's a Compilation"]
    explain.assert_called_once_with("http://localhost:8686", "key", 855, "Eiskalt (2011)")


def test_import_folder_falls_back_to_plain_reason_when_not_explained() -> None:
    artist_response = Mock()
    artist_response.json.return_value = [{"id": 855, "path": "/music/Eisbrecher"}]
    artist_trackfiles_response = Mock()
    artist_trackfiles_response.json.return_value = []
    candidates = [
        {
            "path": "/music/Eisbrecher/Eiskalt (2011)/01.mp3",
            "artist": None,
            "album": None,
            "tracks": [],
            "rejections": [{"reason": "Couldn't find similar album for [/music/Eisbrecher/Eiskalt (2011)]"}],
        },
    ]
    candidates_response = Mock()
    candidates_response.json.return_value = candidates

    with (
        patch(
            "requests.get", side_effect=[artist_response, artist_trackfiles_response, candidates_response]
        ),
        patch("lidarr.explain_missing_album", return_value=None),
    ):
        imported, skipped, skipped_names = lidarr.import_folder(
            "http://localhost:8686", "key", Path("/music/Eisbrecher/Eiskalt (2011)")
        )

    assert skipped_names == [
        "01.mp3: Couldn't find similar album for [/music/Eisbrecher/Eiskalt (2011)]"
    ]


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


def test_import_folder_clears_stale_trackfile_and_retries(tmp_path: Path) -> None:
    # First scan: Lidarr rejects the MP3 because it still has a TrackFile
    # record for the FLAC this tool converted and deleted (the file is
    # genuinely gone locally - only "a.mp3" exists, "a.flac" doesn't).
    (tmp_path / "a.mp3").write_bytes(b"data")

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
    second_scan = [_matched_candidate("/music/a.mp3", album_id=5)]
    first_scan_response = Mock()
    first_scan_response.json.return_value = first_scan
    trackfiles_response = Mock()
    trackfiles_response.json.return_value = [{"id": 99, "path": "/music/a.flac"}]
    second_scan_response = Mock()
    second_scan_response.json.return_value = second_scan
    import_wait_response = Mock()
    import_wait_response.json.return_value = {"status": "completed"}
    import_post_response = Mock()
    import_post_response.json.return_value = {"id": 2}

    with (
        patch(
            "requests.get",
            side_effect=[
                _no_artist_match_response(),
                first_scan_response,
                trackfiles_response,
                second_scan_response,
                import_wait_response,
            ],
        ),
        patch("requests.delete", return_value=Mock()) as delete,
        patch("requests.post", return_value=import_post_response),
    ):
        imported, skipped, skipped_names = lidarr.import_folder(
            "http://localhost:8686", "key", Path("/music"), local_root=str(tmp_path), lidarr_root="/music"
        )

    assert imported == 1
    assert skipped == 0
    assert skipped_names == []
    delete.assert_called_once()


def test_import_folder_raises_on_lidarr_reported_failure() -> None:
    candidates = [_matched_candidate("/music/a.mp3")]
    candidates_response = Mock()
    candidates_response.json.return_value = candidates
    import_wait_response = Mock()
    import_wait_response.json.return_value = {"status": "failed", "message": "disk full"}
    import_post_response = Mock()
    import_post_response.json.return_value = {"id": 2}

    with (
        patch(
            "requests.get", side_effect=[_no_artist_match_response(), candidates_response, import_wait_response]
        ),
        patch("requests.post", return_value=import_post_response),
    ):
        with pytest.raises(lidarr.LidarrError, match="disk full"):
            lidarr.import_folder("http://localhost:8686", "key", Path("/music"))
