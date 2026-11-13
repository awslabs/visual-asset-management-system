"""APIClient methods and helpers of Download as Zip: requests sent, error mapping, the feature
precheck, the business 429, the cancel/delete state checks and the archive stream."""

import json
from unittest.mock import MagicMock

import pytest
import requests

from vamscli import constants
from vamscli.utils.api_client import APIClient
from vamscli.utils.exceptions import (
    APIError,
    AssetNotFoundError,
    InvalidZipDownloadRequestError,
    RetryExhaustedError,
    ZipDownloadDisabledError,
    ZipDownloadExpiredError,
    ZipDownloadFailedError,
    ZipDownloadLimitError,
    ZipDownloadNotFoundError,
    ZipDownloadNotReadyError,
    ZipDownloadStateError,
    ZipDownloadTimeoutError,
    ZipDownloadTransferError,
)
from vamscli.utils import zip_downloads as zd

BASE = "https://api.example.com"
PRESIGNED = "https://bucket.s3.amazonaws.com/vams.preparedDownloads/a.zip?X-Amz-Signature=abc123secret"


def _response(body=None, status=200, headers=None, url=BASE):
    response = requests.Response()
    response.status_code = status
    response.url = url
    response._content = b"" if body is None else json.dumps(body).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    for name, value in (headers or {}).items():
        response.headers[name] = value
    return response


def _client(enabled=("ZIPDOWNLOADS", "AUTHPROVIDER_COGNITO"), read=True):
    profile_manager = MagicMock()
    profile_manager.is_override_token.return_value = False
    profile_manager.is_token_expired.return_value = False
    profile_manager.load_auth_profile.return_value = {"access_token": "t"}
    profile_manager.get_feature_switches_info.return_value = (
        {"has_feature_switches": True, "enabled": list(enabled)} if read
        else {"has_feature_switches": False, "enabled": []})
    return APIClient(BASE, profile_manager=profile_manager)


class _Wire:
    """Records every send; the last response repeats."""

    def __init__(self, monkeypatch, client, *responses):
        self.sends = []
        self._responses = list(responses)

        def request(method, url, **kwargs):
            self.sends.append((method, url, kwargs))
            return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]

        monkeypatch.setattr(client.session, "request", request)


JOB = {"requestId": "r1", "databaseId": "db", "assetId": "a", "status": "QUEUED",
       "archiveFileName": "a.zip", "progress": {}}


class TestRequests:
    def test_create_posts_the_body_to_the_asset_route(self, monkeypatch):
        client = _client()
        wire = _Wire(monkeypatch, client, _response(JOB))
        body = {"selection": [{"type": "asset", "relativePath": "/"}], "archiveFormat": "zip"}

        assert client.create_zip_download("db", "a", body) == JOB

        method, url, kwargs = wire.sends[0]
        assert method == "POST"
        assert url == BASE + "/database/db/assets/a/download/zip"
        assert kwargs["json"] == body

    def test_list_global_and_per_asset_with_paging(self, monkeypatch):
        client = _client()
        wire = _Wire(monkeypatch, client, _response({"Items": [], "NextToken": "n"}))

        client.list_zip_downloads(max_items=5, page_size=2, starting_token="tok")
        client.list_zip_downloads("db", "a")

        assert wire.sends[0][1] == BASE + "/download/zip"
        assert wire.sends[0][2]["params"] == {"maxItems": 5, "pageSize": 2, "startingToken": "tok"}
        assert wire.sends[1][1] == BASE + "/database/db/assets/a/download/zip"
        assert wire.sends[1][2]["params"] == {}

    def test_list_all_users_calls_the_all_users_route(self, monkeypatch):
        client = _client()
        wire = _Wire(monkeypatch, client, _response({"Items": []}))
        client.list_zip_downloads("db", "a", page_size=3, all_users=True)
        assert wire.sends[0][:2] == ("GET", BASE + "/database/db/assets/a/download/zip-all-users")
        assert wire.sends[0][2]["params"] == {"pageSize": 3}

    def test_list_all_users_needs_the_asset(self, monkeypatch):
        client = _client()
        wire = _Wire(monkeypatch, client, _response({"Items": []}))
        with pytest.raises(InvalidZipDownloadRequestError):
            client.list_zip_downloads(all_users=True)
        assert wire.sends == []

    def test_list_refuses_half_a_scope(self, monkeypatch):
        client = _client()
        wire = _Wire(monkeypatch, client, _response({"Items": []}))
        with pytest.raises(InvalidZipDownloadRequestError):
            client.list_zip_downloads(database_id="db")
        assert wire.sends == []

    def test_get_and_url_routes(self, monkeypatch):
        client = _client()
        wire = _Wire(monkeypatch, client, _response(JOB),
                     _response({"url": PRESIGNED, "expiresIn": 900, "archiveFileName": "a.zip"}))

        client.get_zip_download("db", "a", "r1")
        client.get_zip_download_url("db", "a", "r1")

        assert wire.sends[0][:2] == ("GET", BASE + "/database/db/assets/a/download/zip/r1")
        assert wire.sends[1][:2] == ("POST", BASE + "/database/db/assets/a/download/zip/r1/url")


class TestFeaturePrecheck:
    def test_switch_absent_refuses_before_any_request(self, monkeypatch):
        client = _client(enabled=("AUTHPROVIDER_COGNITO",))
        wire = _Wire(monkeypatch, client, _response(JOB))

        with pytest.raises(ZipDownloadDisabledError) as info:
            client.get_zip_download("db", "a", "r1")

        assert str(info.value) == constants.ZIP_DOWNLOADS_DISABLED_MESSAGE
        assert wire.sends == []

    def test_unread_switches_fall_through_to_the_404_body(self, monkeypatch):
        client = _client(read=False)
        wire = _Wire(monkeypatch, client, _response(
            {"message": constants.ZIP_DOWNLOADS_DISABLED_API_MESSAGE}, status=404))

        with pytest.raises(ZipDownloadDisabledError) as info:
            client.list_zip_downloads()

        assert str(info.value) == "Zip downloads are not available for this deployment"
        assert len(wire.sends) == 1

    def test_no_profile_manager_does_not_break_the_precheck(self):
        client = _client()
        client.profile_manager = None
        client._zip_downloads_precheck()  # no exception


class TestErrorMapping:
    @pytest.mark.parametrize("status,body,expected", [
        (404, {"message": "Zip download not found"}, ZipDownloadNotFoundError),
        (404, {"message": "Asset not found"}, AssetNotFoundError),
        (400, {"message": "relativePath must not contain '..'"}, InvalidZipDownloadRequestError),
        (410, {"message": "a file version is archived"}, InvalidZipDownloadRequestError),
        (500, {"message": "The archive job could not be started"}, APIError),
    ])
    def test_create_errors(self, monkeypatch, status, body, expected):
        client = _client()
        _Wire(monkeypatch, client, _response(body, status=status))
        with pytest.raises(expected):
            client.create_zip_download("db", "a", {"selection": []})

    @pytest.mark.parametrize("status,expected", [
        (409, ZipDownloadNotReadyError), (410, ZipDownloadExpiredError)])
    def test_url_errors(self, monkeypatch, status, expected):
        client = _client()
        _Wire(monkeypatch, client, _response({"message": "x"}, status=status))
        with pytest.raises(expected):
            client.get_zip_download_url("db", "a", "r1")

    def test_the_active_cap_429_is_not_retried(self, monkeypatch):
        client = _client()
        wire = _Wire(monkeypatch, client, _response(
            {"message": constants.ZIP_DOWNLOAD_CAP_REACHED_API_MESSAGE}, status=429))

        with pytest.raises(ZipDownloadLimitError):
            client.create_zip_download("db", "a", {"selection": []})
        assert len(wire.sends) == 1

    def test_a_throttling_429_keeps_the_backoff(self, monkeypatch):
        """Control: only the exact cap message skips the retry."""
        client = _client()
        wire = _Wire(monkeypatch, client, _response({"message": "Too Many Requests"}, status=429))
        monkeypatch.setattr("vamscli.utils.retry_config.RetryConfig.sleep_with_progress",
                            lambda *a, **k: None)

        with pytest.raises(RetryExhaustedError):
            client.get_zip_download("db", "a", "r1")
        assert len(wire.sends) > 1


class TestCancelDelete:
    @pytest.mark.parametrize("status", ["QUEUED", "RUNNING"])
    def test_cancel_an_active_job(self, monkeypatch, status):
        client = _client()
        wire = _Wire(monkeypatch, client, _response({**JOB, "status": status}),
                     _response({"message": "Zip download cancelled", "action": "cancelled"}))

        assert client.cancel_zip_download("db", "a", "r1")["action"] == "cancelled"
        assert [s[0] for s in wire.sends] == ["GET", "DELETE"]

    @pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "CANCELLED"])
    def test_cancel_refuses_a_finished_job(self, monkeypatch, status):
        client = _client()
        wire = _Wire(monkeypatch, client, _response({**JOB, "status": status}))
        with pytest.raises(ZipDownloadStateError):
            client.cancel_zip_download("db", "a", "r1")
        assert [s[0] for s in wire.sends] == ["GET"]

    @pytest.mark.parametrize("status", ["QUEUED", "RUNNING"])
    def test_delete_refuses_an_active_job(self, monkeypatch, status):
        client = _client()
        wire = _Wire(monkeypatch, client, _response({**JOB, "status": status}))
        with pytest.raises(ZipDownloadStateError):
            client.delete_zip_download("db", "a", "r1")
        assert [s[0] for s in wire.sends] == ["GET"]

    def test_delete_a_finished_job(self, monkeypatch):
        client = _client()
        wire = _Wire(monkeypatch, client, _response({**JOB, "status": "SUCCEEDED"}),
                     _response({"message": "Zip download deleted", "action": "deleted"}))
        assert client.delete_zip_download("db", "a", "r1")["action"] == "deleted"
        assert wire.sends[1][:2] == ("DELETE", BASE + "/database/db/assets/a/download/zip/r1")
        assert not wire.sends[1][2].get("params"), "a delete carries no cancel intent"

    def test_cancel_sends_the_cancel_intent(self, monkeypatch):
        """#1125 D23: the DELETE says it is a cancel, so the API never deletes a job that finished
        between the state check and the call."""
        client = _client()
        wire = _Wire(monkeypatch, client, _response({**JOB, "status": "RUNNING"}),
                     _response({"message": "Zip download cancelled", "action": "cancelled"}))
        client.cancel_zip_download("db", "a", "r1")
        method, url, kwargs = wire.sends[1]
        assert (method, url) == ("DELETE", BASE + "/database/db/assets/a/download/zip/r1")
        assert kwargs["params"] == {constants.ZIP_DOWNLOAD_INTENT_PARAM: constants.ZIP_DOWNLOAD_CANCEL_INTENT}
        assert kwargs["params"] == {"intent": "cancel"}

    def test_cancel_of_a_job_that_finished_after_the_check_reports_ended(self, monkeypatch):
        client = _client()
        ended = {"message": "The zip download finished before it could be cancelled",
                 "action": "ended", "status": "SUCCEEDED"}
        wire = _Wire(monkeypatch, client, _response({**JOB, "status": "RUNNING"}), _response(ended))
        assert client.cancel_zip_download("db", "a", "r1") == ended
        assert wire.sends[1][2]["params"] == {"intent": "cancel"}


class _Stream:
    def __init__(self, status=200, chunks=(b"abc", b"def"), headers=None, raise_after=None):
        self.status_code = status
        self.headers = headers or {}
        self._chunks = chunks
        self._raise_after = raise_after

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=None):
        for index, chunk in enumerate(self._chunks):
            if self._raise_after is not None and index == self._raise_after:
                raise requests.exceptions.ConnectionError(f"Max retries exceeded with url: {PRESIGNED}")
            yield chunk


class TestArchiveStream:
    def test_writes_atomically_without_auth(self, monkeypatch, tmp_path):
        client = _client()
        seen = {}

        def get(url, **kwargs):
            seen.update(kwargs, url=url)
            return _Stream()

        monkeypatch.setattr(client.session, "get", get)
        target = tmp_path / "a.zip"
        progress = []

        written = client.download_zip_archive(PRESIGNED, str(target), expected_size=6,
                                              progress_callback=lambda d, t: progress.append((d, t)))

        assert written == 6 and target.read_bytes() == b"abcdef"
        assert "headers" not in seen and seen["stream"] is True
        assert progress[-1] == (6, 6)
        assert not (tmp_path / "a.zip.vamsdownload").exists()

    def test_size_mismatch_leaves_nothing(self, monkeypatch, tmp_path):
        client = _client()
        monkeypatch.setattr(client.session, "get", lambda url, **kw: _Stream())
        target = tmp_path / "a.zip"
        with pytest.raises(ZipDownloadTransferError):
            client.download_zip_archive(PRESIGNED, str(target), expected_size=99)
        assert list(tmp_path.iterdir()) == []

    def test_connection_error_never_carries_the_url(self, monkeypatch, tmp_path):
        client = _client()
        monkeypatch.setattr(client.session, "get", lambda url, **kw: _Stream(raise_after=1))
        with pytest.raises(ZipDownloadTransferError) as info:
            client.download_zip_archive(PRESIGNED, str(tmp_path / "a.zip"))
        assert "X-Amz-Signature" not in str(info.value) and "s3.amazonaws.com" not in str(info.value)
        assert list(tmp_path.iterdir()) == []

    def test_refused_link(self, monkeypatch, tmp_path):
        client = _client()
        monkeypatch.setattr(client.session, "get", lambda url, **kw: _Stream(status=403))
        with pytest.raises(ZipDownloadTransferError, match="expired"):
            client.download_zip_archive(PRESIGNED, str(tmp_path / "a.zip"))


class TestSelection:
    def test_file_specs(self):
        assert zd.parse_file_spec("/a/b.glb") == {"type": "file", "relativePath": "/a/b.glb"}
        assert zd.parse_file_spec("a/b.glb@v1.X_y-z") == {
            "type": "file", "relativePath": "/a/b.glb", "versionId": "v1.X_y-z"}
        # A path containing '@' ends with a bare '@' to stay unpinned.
        assert zd.parse_file_spec("/a@b.glb@") == {"type": "file", "relativePath": "/a@b.glb"}
        # A suffix that is not a version id is part of the path.
        assert zd.parse_file_spec("/x@y z.glb") == {"type": "file", "relativePath": "/x@y z.glb"}

    def test_folder_and_whole_asset(self):
        assert zd.parse_folder("textures") == {"type": "folder", "relativePath": "/textures/"}
        assert zd.build_selection([], [], True) == [{"type": "asset", "relativePath": "/"}]
        with pytest.raises(InvalidZipDownloadRequestError):
            zd.build_selection(["/a"], [], True)
        with pytest.raises(InvalidZipDownloadRequestError):
            zd.build_selection([], [], False)
        with pytest.raises(InvalidZipDownloadRequestError):
            zd.parse_folder("/")
        with pytest.raises(InvalidZipDownloadRequestError):
            zd.parse_file_spec("/dir/")

    def test_selection_entry_cap(self):
        with pytest.raises(InvalidZipDownloadRequestError):
            zd.build_selection([f"/f{i}" for i in range(constants.ZIP_DOWNLOAD_MAX_SELECTION_ENTRIES + 1)],
                               [], False)

    def test_create_body_refusals(self):
        pinned_file = [{"type": "file", "relativePath": "/a", "versionId": "v"}]
        with pytest.raises(InvalidZipDownloadRequestError):
            zd.build_create_request(pinned_file, asset_version_id="1")
        with pytest.raises(InvalidZipDownloadRequestError):
            zd.build_create_request(pinned_file, asset_version_id="1", asset_version_alias="x")
        with pytest.raises(InvalidZipDownloadRequestError):
            zd.build_create_request(pinned_file, archive_format="tar", compression="deflate")
        body = zd.build_create_request([{"type": "asset", "relativePath": "/"}],
                                       asset_version_alias="release", archive_format="tar.gz",
                                       archive_file_name="out")
        assert body == {"selection": [{"type": "asset", "relativePath": "/"}],
                        "archiveFormat": "tar.gz", "compression": "store",
                        "assetVersionIdAlias": "release", "archiveFileName": "out"}


class TestWait:
    def _api(self, *statuses):
        api = MagicMock()
        api.get_zip_download.side_effect = [
            {**JOB, "status": s, "errorMessage": "ZIPDOWNLOAD: /x.exe is blocked" if s == "FAILED" else None}
            for s in statuses]
        return api

    def test_returns_the_succeeded_job(self):
        api = self._api("QUEUED", "RUNNING", "SUCCEEDED")
        sleeps = []
        job = zd.wait_for_zip_download(api, "db", "a", "r1", 60, sleep=sleeps.append,
                                       clock=lambda: 0.0)
        assert job["status"] == "SUCCEEDED" and len(sleeps) == 2

    def test_failed_job_raises_with_fields(self):
        api = self._api("RUNNING", "FAILED")
        with pytest.raises(ZipDownloadFailedError) as info:
            zd.wait_for_zip_download(api, "db", "a", "r1", 60, sleep=lambda s: None, clock=lambda: 0.0)
        assert info.value.json_fields["status"] == "FAILED"
        assert info.value.json_fields["requestId"] == "r1"

    def test_timeout(self):
        api = MagicMock()
        api.get_zip_download.return_value = {**JOB, "status": "RUNNING"}
        ticks = iter([0.0, 100.0])
        with pytest.raises(ZipDownloadTimeoutError) as info:
            zd.wait_for_zip_download(api, "db", "a", "r1", 10, sleep=lambda s: None,
                                     clock=lambda: next(ticks))
        assert info.value.json_fields == {"requestId": "r1", "status": "RUNNING"}


class TestLocalPath:
    def test_directory_uses_the_last_component_of_the_archive_name(self, tmp_path):
        assert zd.resolve_local_path(str(tmp_path), "../../etc/x.zip") == tmp_path / "x.zip"

    def test_existing_file_needs_overwrite(self, tmp_path):
        existing = tmp_path / "x.zip"
        existing.write_bytes(b"")
        with pytest.raises(InvalidZipDownloadRequestError):
            zd.resolve_local_path(str(existing), "x.zip")
        assert zd.resolve_local_path(str(existing), "x.zip", overwrite=True) == existing

    def test_missing_parent(self, tmp_path):
        with pytest.raises(InvalidZipDownloadRequestError):
            zd.resolve_local_path(str(tmp_path / "no" / "x.zip"), "x.zip")
