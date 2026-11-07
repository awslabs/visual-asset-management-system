# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Download as Zip API: authorization, ownership, the disabled switch, create-time refusal rules,
version pinning, the per-user cap, paging, presigned URLs, cancel/delete and the read-time reconcile.

The handlers run against in-memory doubles (zipHarness) that evaluate the real conditional writes,
so a test that a second user cannot read a job reads the table the handler wrote, not a mock call.
"""

import json

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit


@pytest.fixture
def env():
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    e.s3.add("xasset1/a.glb", size=100, version="va1")
    e.s3.add("xasset1/textures/t1.png", size=20, version="vt1", content_type="image/png")
    e.s3.add("xasset1/textures/t2.png", size=30, version="vt2", content_type="image/png")
    return e


def _create(e, body, user="alice", database_id="db1", asset_id="asset1"):
    h.AUTH.user = user
    return e.svc.lambda_handler(h.api_event(
        "POST", h.asset_path(database_id, asset_id), {"databaseId": database_id, "assetId": asset_id},
        body=body), None)


def _job_route(e, method, request_id, user="alice", suffix="", asset_id="asset1"):
    h.AUTH.user = user
    path = h.asset_path("db1", asset_id, f"/{request_id}{suffix}")
    return e.svc.lambda_handler(h.api_event(method, path, {"databaseId": "db1", "assetId": asset_id,
                                                           "requestId": request_id}), None)


def _list(e, user="alice", asset=True, query=None):
    h.AUTH.user = user
    if asset:
        event = h.api_event("GET", h.asset_path(), {"databaseId": "db1", "assetId": "asset1"}, query=query)
    else:
        event = h.api_event("GET", "/download/zip", {}, query=query)
    return e.svc.lambda_handler(event, None)


FILE_SELECTION = {"selection": [{"type": "file", "relativePath": "/a.glb"}]}


class TestAuthorization:
    def test_tier1_denied(self, env):
        h.AUTH.api_allowed = False
        assert _create(env, FILE_SELECTION)["statusCode"] == 403
        assert env.zip.items == {}

    def test_empty_tokens_fail_closed(self, env):
        response = _create(env, FILE_SELECTION, user=None)
        assert response["statusCode"] == 403
        assert env.lam.calls == []

    def test_asset_check_fails_closed_on_empty_tokens(self, env):
        """The Tier-2 helper refuses on empty tokens itself, even where the enforcer would allow."""
        asset = {"databaseId": "db1", "assetId": "asset1"}
        previous = env.svc.claims_and_roles
        try:
            env.svc.claims_and_roles = {"tokens": ["alice"], "roles": []}
            assert env.svc._can_read_asset(asset) is True
            env.svc.claims_and_roles = {"tokens": [], "roles": []}
            assert env.svc._can_read_asset(asset) is False
            env.svc.claims_and_roles = {"roles": []}
            assert env.svc._can_read_asset(asset) is False
        finally:
            env.svc.claims_and_roles = previous

    def test_tier2_asset_get_denied_on_create(self, env):
        h.AUTH.denied_assets.add("asset1")
        assert _create(env, FILE_SELECTION)["statusCode"] == 403
        assert env.zip.items == {} and env.s3.puts == {}

    def test_disabled_answers_404_after_tier1(self):
        h.AUTH.__init__()
        e = h.load_handlers(enabled=False)
        h.put_asset(e)
        response = _create(e, FILE_SELECTION)
        assert response["statusCode"] == 404
        assert h.body_of(response)["message"] == e.zd.MSG_DISABLED
        h.AUTH.api_allowed = False
        assert _create(e, FILE_SELECTION)["statusCode"] == 403

    def test_disabled_global_list_404(self):
        h.AUTH.__init__()
        e = h.load_handlers(enabled=False)
        assert _list(e, asset=False)["statusCode"] == 404

    def test_second_user_cannot_get_url_or_delete(self, env):
        request_id = h.body_of(_create(env, FILE_SELECTION))["requestId"]
        for method, suffix in (("GET", ""), ("POST", "/url"), ("DELETE", "")):
            response = _job_route(env, method, request_id, user="mallory", suffix=suffix)
            assert response["statusCode"] == 404, (method, suffix)
            assert h.body_of(response)["message"] == env.zd.MSG_NOT_FOUND
        assert len(env.zip.items) == 1

    def test_every_ownership_miss_is_audited_with_its_operation(self, env):
        # #1125 L3: the 404 for another user's job is audited on get, url and delete alike.
        request_id = h.body_of(_create(env, FILE_SELECTION))["requestId"]
        env.audit.reset_mock()
        for method, suffix, operation in (("GET", "", "get"), ("POST", "/url", "url"),
                                          ("DELETE", "", "delete")):
            env.audit.reset_mock()
            _job_route(env, method, request_id, user="mallory", suffix=suffix)
            denials = [c for c in env.audit.call_args_list if c[0][1] == "zipDownloadNotFound"]
            assert len(denials) == 1, (method, suffix)
            assert denials[0][0][2] == {"databaseId": "db1", "assetId": "asset1",
                                        "requestId": request_id, "operation": operation}

    def test_the_owners_own_lookup_writes_no_denial(self, env):
        request_id = h.body_of(_create(env, FILE_SELECTION))["requestId"]
        env.audit.reset_mock()
        assert _job_route(env, "GET", request_id)["statusCode"] == 200
        assert _job_route(env, "GET", request_id, user="mallory")["statusCode"] == 404
        # Exactly one denial across both reads: mallory's, so the owner's read wrote none.
        denials = [c[0][2]["requestId"] for c in env.audit.call_args_list
                   if c[0][1] == "zipDownloadNotFound"]
        assert denials == [request_id]

    def test_second_user_list_is_empty(self, env):
        _create(env, FILE_SELECTION)
        assert h.body_of(_list(env, user="mallory"))["Items"] == []
        assert h.body_of(_list(env, user="mallory", asset=False))["Items"] == []
        assert len(h.body_of(_list(env, user="alice"))["Items"]) == 1

    def test_job_under_another_asset_path_is_not_found(self, env):
        h.put_asset(env, asset_id="asset2")
        request_id = h.body_of(_create(env, FILE_SELECTION))["requestId"]
        assert _job_route(env, "GET", request_id, asset_id="asset2")["statusCode"] == 404

    def test_lost_asset_access_hides_job(self, env):
        request_id = h.body_of(_create(env, FILE_SELECTION))["requestId"]
        h.AUTH.denied_assets.add("asset1")
        assert _job_route(env, "GET", request_id)["statusCode"] == 404
        assert _job_route(env, "POST", request_id, suffix="/url")["statusCode"] == 404
        assert h.body_of(_list(env, asset=False))["Items"] == []

    def test_job_response_never_exposes_internal_fields(self, env):
        item = h.body_of(_create(env, FILE_SELECTION))
        for hidden in ("userId", "archiveKey", "manifestKey", "workflowExecutionId", "userAssetKey"):
            assert hidden not in item
        row = next(iter(env.zip.items.values()))
        h.record_upload_token(row)
        listed = h.body_of(_job_route(env, "GET", item["requestId"]))
        assert "archiveUploadToken" not in listed and h.UPLOAD_TOKEN not in json.dumps(listed)


class TestCreate:
    def test_file_job_writes_manifest_row_and_launches(self, env):
        response = _create(env, {"selection": [{"type": "file", "relativePath": "/a.glb"}],
                                 "archiveFileName": "../My Export?.zip"})
        assert response["statusCode"] == 200
        item = h.body_of(response)
        assert item["status"] == "QUEUED"
        assert item["archiveFileName"] == "My Export_.zip"
        assert item["progress"]["filesTotal"] == 1 and item["progress"]["bytesTotal"] == 100
        row = next(iter(env.zip.items.values()))
        assert row["userId"] == "alice" and row["workflowExecutionId"] == "exec-1"
        assert row["userAssetKey"] == "alice#db1#asset1"
        assert row["archiveKey"].startswith("vams.preparedDownloads/db1/asset1/")
        manifest = json.loads(env.s3.puts[(h.AUX, row["manifestKey"])])
        assert manifest["entries"] == [{"type": "file", "key": "xasset1/a.glb", "relativePath": "/a.glb"}]
        assert ".exe" in manifest["blockedExtensions"]
        env.audit.assert_called()
        assert env.audit.call_args[0][1] == "zipDownloadCreate"

    def test_launch_event_is_a_marked_system_cross_call(self, env):
        _create(env, {"selection": [{"type": "folder", "relativePath": "/textures/"}]})
        function, invocation, event = env.lam.calls[0]
        assert function == "executeFn" and invocation == "RequestResponse"
        assert event["lambdaCrossCall"] == {"userName": "SYSTEM_USER", "source": "vamsSystemPipeline"}
        assert event["systemExecution"]["triggerType"] == "zipDownload"
        context = event["systemExecution"]["context"]
        assert set(context) == {"v", "requestId", "userId", "databaseId", "assetId", "manifestKey"}
        assert len(json.dumps(context)) < 4096
        assert event["pathParameters"] == {"workflowDatabaseId": "GLOBAL", "workflowId": "utility-zip-archive"}
        assert json.loads(event["body"])["inputFiles"] == [
            {"databaseId": "db1", "assetId": "asset1", "relativeFileKey": "/textures/"}]

    def test_launch_failure_fails_the_job(self, env):
        env.lam.responses["executeFn"] = (400, {"message": "Workflow is disabled"})
        response = _create(env, FILE_SELECTION)
        assert response["statusCode"] == 500
        row = next(iter(env.zip.items.values()))
        assert row["status"] == "FAILED" and row["errorMessage"] == env.zd.MSG_LAUNCH_FAILED

    def test_folder_totals_from_prescan(self, env):
        item = h.body_of(_create(env, {"selection": [{"type": "folder", "relativePath": "/textures/"}]}))
        assert item["progress"]["filesTotal"] == 2 and item["progress"]["bytesTotal"] == 50

    def test_whole_asset(self, env):
        item = h.body_of(_create(env, {"selection": [{"type": "asset", "relativePath": "/"}]}))
        assert item["selectionSummary"]["wholeAsset"] is True
        assert item["progress"]["filesTotal"] == 3

    def test_not_distributable_refused(self, env):
        h.put_asset(env, distributable=False)
        response = _create(env, FILE_SELECTION)
        assert response["statusCode"] == 400
        assert env.zip.items == {}

    def test_missing_asset_404(self, env):
        assert _create(env, FILE_SELECTION, asset_id="nope")["statusCode"] == 404

    @pytest.mark.parametrize("selection,needle", [
        ([{"type": "file", "relativePath": "a.glb"}], "begin with '/'"),
        ([{"type": "file", "relativePath": "/../x"}], "segments"),
        ([{"type": "file", "relativePath": "/a\\b"}], "backslashes"),
        ([{"type": "file", "relativePath": "/a\x00b"}], "control"),
        ([{"type": "folder", "relativePath": "/textures"}], "folder"),
        ([{"type": "asset", "relativePath": "/"}, {"type": "file", "relativePath": "/a.glb"}], "only entry"),
        ([{"type": "folder", "relativePath": "/t/", "versionId": "v1"}], "file entries"),
    ])
    def test_selection_validation(self, env, selection, needle):
        response = _create(env, {"selection": selection})
        assert response["statusCode"] == 400
        assert needle in h.body_of(response)["message"]

    def test_both_pin_forms_refused(self, env):
        response = _create(env, {"selection": [{"type": "file", "relativePath": "/a.glb", "versionId": "va1"}],
                                 "assetVersionId": "1"})
        assert response["statusCode"] == 400
        response = _create(env, {"selection": FILE_SELECTION["selection"], "assetVersionId": "1",
                                 "assetVersionIdAlias": "RC1"})
        assert response["statusCode"] == 400

    def test_deflate_only_for_zip(self, env):
        response = _create(env, {"selection": FILE_SELECTION["selection"], "archiveFormat": "tar",
                                 "compression": "deflate"})
        assert response["statusCode"] == 400

    def test_blocked_extension_explicit_file_refused(self, env):
        env.s3.add("xasset1/run.exe", size=5)
        response = _create(env, {"selection": [{"type": "file", "relativePath": "/run.exe"}]})
        assert response["statusCode"] == 400
        assert env.zip.items == {}

    def test_blocked_extension_inside_folder_refuses_whole_job(self, env):
        env.s3.add("xasset1/textures/tool.exe", size=5)
        response = _create(env, {"selection": [{"type": "folder", "relativePath": "/textures/"}]})
        assert response["statusCode"] == 400
        assert env.zip.items == {} and env.lam.calls == []

    def test_glacier_inside_folder_refuses_whole_job(self, env):
        env.s3.add("xasset1/textures/cold.png", size=5, storage_class="GLACIER")
        response = _create(env, {"selection": [{"type": "folder", "relativePath": "/textures/"}]})
        assert response["statusCode"] == 400
        assert "archive storage class" in h.body_of(response)["message"]

    def test_archived_metadata_file_refused(self, env):
        env.s3.add("xasset1/old.glb", metadata={"vams-status": "archived"})
        response = _create(env, {"selection": [{"type": "file", "relativePath": "/old.glb"}]})
        assert response["statusCode"] == 400
        assert "archived" in h.body_of(response)["message"]

    def test_delete_marker_version_refused_410(self, env):
        env.s3.add("xasset1/a.glb", version="dm1", delete_marker=True)
        response = _create(env, {"selection": [{"type": "file", "relativePath": "/a.glb", "versionId": "dm1"}]})
        assert response["statusCode"] == 410

    def test_empty_folder_refused(self, env):
        response = _create(env, {"selection": [{"type": "folder", "relativePath": "/empty/"}]})
        assert response["statusCode"] == 400

    def test_file_limit(self, env):
        e = h.load_handlers(env={"ZIP_DOWNLOAD_MAX_FILES_PER_REQUEST": "1"})
        h.put_asset(e)
        e.s3.objects = env.s3.objects
        response = _create(e, {"selection": [{"type": "folder", "relativePath": "/textures/"}]})
        assert response["statusCode"] == 400
        assert "more than 1 files" in h.body_of(response)["message"]

    def test_byte_limit(self, env):
        e = h.load_handlers(env={"ZIP_DOWNLOAD_MAX_TOTAL_BYTES_PER_REQUEST": "40"})
        h.put_asset(e)
        e.s3.objects = env.s3.objects
        response = _create(e, {"selection": [{"type": "folder", "relativePath": "/textures/"}]})
        assert response["statusCode"] == 400

    def test_active_cap_429(self, env):
        for _ in range(5):
            assert _create(env, FILE_SELECTION)["statusCode"] == 200
        response = _create(env, FILE_SELECTION)
        assert response["statusCode"] == 429
        assert h.body_of(response)["message"] == env.zd.MSG_CAP_REACHED
        # A finished job frees a slot.
        row = next(iter(env.zip.items.values()))
        row["status"] = "SUCCEEDED"
        assert _create(env, FILE_SELECTION)["statusCode"] == 200

    def test_cap_is_per_user(self, env):
        for _ in range(5):
            _create(env, FILE_SELECTION)
        assert _create(env, FILE_SELECTION, user="bob")["statusCode"] == 200


class TestVersionPinning:
    def _versions(self, env, monkeypatch, files, alias_map=None):
        import types as _t
        import sys
        fake = _t.ModuleType("handlers.assets.assetVersions")
        fake.validate_asset_version_exists = lambda db, a, v: True
        fake.get_asset_file_versions = lambda db, a, v: {"files": files}
        fake.resolve_asset_version_id_from_alias = lambda db, a, alias: (alias_map or {})[alias]
        monkeypatch.setitem(sys.modules, "handlers.assets.assetVersions", fake)

    def test_per_file_version_kept(self, env):
        env.s3.add("xasset1/a.glb", size=70, version="va2")
        item = h.body_of(_create(env, {"selection": [{"type": "file", "relativePath": "/a.glb", "versionId": "va1"}]}))
        assert item["progress"]["bytesTotal"] == 100
        row = next(iter(env.zip.items.values()))
        manifest = json.loads(env.s3.puts[(h.AUX, row["manifestKey"])])
        assert manifest["entries"][0]["versionId"] == "va1"

    def test_asset_version_resolves_whole_asset(self, env, monkeypatch):
        self._versions(env, monkeypatch, [
            {"relativeKey": "/a.glb", "versionId": "va1", "size": 100},
            {"relativeKey": "textures/t1.png", "versionId": "vt1", "size": 20}])
        response = _create(env, {"selection": [{"type": "asset", "relativePath": "/"}], "assetVersionId": "3"})
        assert response["statusCode"] == 200
        row = next(iter(env.zip.items.values()))
        assert row["assetVersionId"] == "3"
        manifest = json.loads(env.s3.puts[(h.AUX, row["manifestKey"])])
        assert manifest["entries"] == []
        assert [(p["relativePath"], p["versionId"]) for p in manifest["pinnedFiles"]] == [
            ("/a.glb", "va1"), ("/textures/t1.png", "vt1")]
        # A pinned run launches on the whole asset; the manifest is the authority.
        assert json.loads(env.lam.calls[0][2]["body"])["inputFiles"][0]["relativeFileKey"] == "/"

    def test_alias_resolution(self, env, monkeypatch):
        self._versions(env, monkeypatch, [{"relativeKey": "/a.glb", "versionId": "va1", "size": 100}],
                       alias_map={"RC1": "2"})
        response = _create(env, {"selection": FILE_SELECTION["selection"], "assetVersionIdAlias": "RC1"})
        assert response["statusCode"] == 200
        assert next(iter(env.zip.items.values()))["assetVersionId"] == "2"

    def test_pinned_folder_filters_snapshot(self, env, monkeypatch):
        self._versions(env, monkeypatch, [
            {"relativeKey": "/a.glb", "versionId": "va1", "size": 100},
            {"relativeKey": "/textures/t1.png", "versionId": "vt1", "size": 20}])
        _create(env, {"selection": [{"type": "folder", "relativePath": "/textures/"}], "assetVersionId": "3"})
        manifest = json.loads(next(iter(env.s3.puts.values())))
        assert [p["relativePath"] for p in manifest["pinnedFiles"]] == ["/textures/t1.png"]

    def test_pinned_delete_marker_refused(self, env, monkeypatch):
        env.s3.add("xasset1/gone.glb", version="dm", delete_marker=True)
        self._versions(env, monkeypatch, [{"relativeKey": "/gone.glb", "versionId": "dm", "size": 1}])
        response = _create(env, {"selection": [{"type": "asset", "relativePath": "/"}], "assetVersionId": "3"})
        assert response["statusCode"] == 410
        assert env.zip.items == {}

    def test_pinned_archived_refused(self, env, monkeypatch):
        env.s3.add("xasset1/cold.glb", version="vc", storage_class="DEEP_ARCHIVE")
        self._versions(env, monkeypatch, [{"relativeKey": "/cold.glb", "versionId": "vc", "size": 1}])
        response = _create(env, {"selection": [{"type": "asset", "relativePath": "/"}], "assetVersionId": "3"})
        assert response["statusCode"] == 400

    def test_file_missing_from_version(self, env, monkeypatch):
        self._versions(env, monkeypatch, [{"relativeKey": "/other.glb", "versionId": "v", "size": 1}])
        response = _create(env, {"selection": FILE_SELECTION["selection"], "assetVersionId": "3"})
        assert response["statusCode"] == 400
        assert "3" not in h.body_of(response)["message"].replace("VAMS General Error: ", "")


class TestListPaging:
    def test_newest_first_and_token_round_trip(self, env):
        ids = [h.body_of(_create(env, FILE_SELECTION))["requestId"] for _ in range(3)]
        for row in env.zip.items.values():
            row["status"] = "SUCCEEDED"
        page1 = h.body_of(_list(env, query={"maxItems": "2", "pageSize": "2"}))
        # Newest first is descending requestId (two jobs created in the same millisecond order by
        # their random suffix).
        newest_first = sorted(ids, reverse=True)
        assert [i["requestId"] for i in page1["Items"]] == newest_first[:2]
        page2 = h.body_of(_list(env, query={"maxItems": "2", "pageSize": "2",
                                            "startingToken": page1["NextToken"]}))
        assert [i["requestId"] for i in page2["Items"]] == newest_first[2:]
        assert "NextToken" not in page2

    def test_global_list_pages(self, env):
        _create(env, FILE_SELECTION)
        _create(env, FILE_SELECTION)
        page1 = h.body_of(_list(env, asset=False, query={"maxItems": "1"}))
        page2 = h.body_of(_list(env, asset=False, query={"maxItems": "1", "startingToken": page1["NextToken"]}))
        assert page1["Items"][0]["requestId"] != page2["Items"][0]["requestId"]

    def test_token_of_another_user_refused(self, env):
        _create(env, FILE_SELECTION)
        forged = h.encode_token({"userId": "bob", "requestId": "zzzzzzzzz-" + "0" * 32})
        response = _list(env, asset=False, query={"startingToken": forged})
        assert response["statusCode"] == 400

    def test_garbage_token_refused(self, env):
        assert _list(env, asset=False, query={"startingToken": "not-base64!"})["statusCode"] == 400

    def test_expired_rows_hidden(self, env):
        _create(env, FILE_SELECTION)
        next(iter(env.zip.items.values()))["expiresAt"] = 1
        assert h.body_of(_list(env))["Items"] == []


class TestUrl:
    def _succeeded(self, env):
        request_id = h.body_of(_create(env, FILE_SELECTION))["requestId"]
        row = next(iter(env.zip.items.values()))
        row["status"] = "SUCCEEDED"
        env.s3.add(row["archiveKey"], size=4242, bucket=h.AUX)
        return request_id, row

    def test_url_issued_with_attachment(self, env):
        request_id, row = self._succeeded(env)
        response = _job_route(env, "POST", request_id, suffix="/url")
        assert response["statusCode"] == 200
        body = h.body_of(response)
        assert body["expiresIn"] == 900 and body["archiveSizeBytes"] == 4242
        op, params, expires = env.s3.presigned[0]
        assert params["Bucket"] == h.AUX and params["Key"] == row["archiveKey"]
        assert params["ResponseContentDisposition"].startswith("attachment; filename=")
        # The URL is never written to the audit log.
        logged = json.dumps([c[0][2] for c in env.audit.call_args_list], default=str)
        assert "X-Amz-Signature" not in logged

    def test_url_refused_until_succeeded(self, env):
        request_id = h.body_of(_create(env, FILE_SELECTION))["requestId"]
        assert _job_route(env, "POST", request_id, suffix="/url")["statusCode"] == 409

    def test_url_refused_when_object_gone(self, env):
        request_id, row = self._succeeded(env)
        env.s3.objects.pop((h.AUX, row["archiveKey"]))
        assert _job_route(env, "POST", request_id, suffix="/url")["statusCode"] == 410

    def test_url_refused_when_asset_no_longer_distributable(self, env):
        request_id, _ = self._succeeded(env)
        h.put_asset(env, distributable=False)
        assert _job_route(env, "POST", request_id, suffix="/url")["statusCode"] == 400

    def test_url_refused_when_expired(self, env):
        request_id, row = self._succeeded(env)
        row["expiresAt"] = 1
        assert _job_route(env, "POST", request_id, suffix="/url")["statusCode"] == 404


class TestCancelDelete:
    def test_cancel_running_job(self, env):
        request_id = h.body_of(_create(env, FILE_SELECTION))["requestId"]
        row = next(iter(env.zip.items.values()))
        env.s3.uploads.append({"Bucket": h.AUX, "Key": row["archiveKey"], "UploadId": "u1"})
        response = _job_route(env, "DELETE", request_id)
        assert h.body_of(response)["action"] == "cancelled"
        row = next(iter(env.zip.items.values()))
        assert row["status"] == "CANCELLED" and row["cancelledBy"] == "user"
        abort = [c for c in env.lam.calls if c[0] == "executionServiceFn"]
        assert abort and abort[0][2]["requestContext"]["http"] == {
            "method": "DELETE", "path": "/workflows/executions/exec-1"}
        assert env.s3.aborted == [(h.AUX, row["archiveKey"], "u1")]

    def test_cancel_removes_the_working_objects_at_once(self, env):
        """#1125 D13: the cancel deletes the request manifest AND the resolved manifest the container
        wrote, every version, instead of leaving them to the 7-day lifecycle rule."""
        request_id = h.body_of(_create(env, FILE_SELECTION))["requestId"]
        row = next(iter(env.zip.items.values()))
        manifest_key, resolved_key = env.zd.working_object_keys(row)
        env.s3.add(resolved_key, bucket=h.AUX, version="r1")
        env.s3.add(resolved_key, bucket=h.AUX, version="r2")
        assert (h.AUX, manifest_key) in env.s3.objects
        response = _job_route(env, "DELETE", request_id)
        assert h.body_of(response)["action"] == "cancelled"
        assert (h.AUX, manifest_key) not in env.s3.objects
        assert (h.AUX, resolved_key) not in env.s3.objects

    def test_working_object_cleanup_touches_only_the_two_keys(self, env):
        h.body_of(_create(env, FILE_SELECTION))
        row = next(iter(env.zip.items.values()))
        env.s3.add(row["archiveKey"], bucket=h.AUX)
        env.zd.delete_working_objects(env.s3, h.AUX, row)
        assert [k for (b, k) in env.s3.objects if b == h.AUX] == [row["archiveKey"]]

    def test_delete_finished_job_removes_objects_and_row(self, env):
        request_id = h.body_of(_create(env, FILE_SELECTION))["requestId"]
        row = next(iter(env.zip.items.values()))
        row["status"] = "SUCCEEDED"
        env.s3.add(row["archiveKey"], bucket=h.AUX)
        response = _job_route(env, "DELETE", request_id)
        assert h.body_of(response)["action"] == "deleted"
        assert env.zip.items == {}
        assert not [k for (b, k) in env.s3.objects if b == h.AUX]

    def test_cleanup_never_leaves_the_job_prefix(self, env):
        with pytest.raises(ValueError):
            env.zd.delete_prefix(env.s3, h.AUX, "vams.preparedDownloads/")
        with pytest.raises(ValueError):
            env.zd.abort_open_uploads(env.s3, h.AUX, "db1/")


class TestReconcile:
    def _running(self, env):
        request_id = h.body_of(_create(env, FILE_SELECTION))["requestId"]
        row = next(iter(env.zip.items.values()))
        row["status"] = "RUNNING"
        return request_id, row

    def _execution(self, env, status, error=""):
        env.executions.items[("exec-1", "GLOBAL:utility-zip-archive")] = {
            "workflowExecutionId": "exec-1", "workflowDatabaseId:workflowId": "GLOBAL:utility-zip-archive",
            "executionStatus": status, "executionError": error}

    def test_aborted_run_fails_job_cancelled_from_executions(self, env):
        request_id, _ = self._running(env)
        self._execution(env, "ABORTED")
        item = h.body_of(_job_route(env, "GET", request_id))
        assert item["status"] == "FAILED"
        assert item["errorMessage"] == "Cancelled from workflow executions"
        assert item["cancelledBy"] == "execution"

    def test_succeeded_run_without_archive_fails(self, env):
        request_id, _ = self._running(env)
        self._execution(env, "SUCCEEDED")
        assert h.body_of(_job_route(env, "GET", request_id))["errorMessage"] == env.zd.MSG_NOT_PRODUCED

    def test_succeeded_run_with_archive_succeeds(self, env):
        request_id, row = self._running(env)
        h.land_archive(env, row, size=99)
        self._execution(env, "SUCCEEDED")
        item = h.body_of(_job_route(env, "GET", request_id))
        assert item["status"] == "SUCCEEDED" and item["archiveSizeBytes"] == 99

    def test_raw_execution_error_never_shown(self, env):
        request_id, _ = self._running(env)
        self._execution(env, "FAILED", "States.TaskFailed arn:aws:states:us-east-1:123:secret")
        item = h.body_of(_job_route(env, "GET", request_id))
        assert item["errorMessage"] == env.zd.MSG_JOB_FAILED

    def test_container_reason_shown(self, env):
        request_id, _ = self._running(env)
        self._execution(env, "FAILED", "ZIPDOWNLOAD: /x.exe has a blocked file type")
        assert h.body_of(_job_route(env, "GET", request_id))["errorMessage"] == "/x.exe has a blocked file type"

    def test_running_run_left_alone(self, env):
        request_id, _ = self._running(env)
        self._execution(env, "RUNNING")
        assert h.body_of(_job_route(env, "GET", request_id))["status"] == "RUNNING"

    def test_terminal_row_is_final(self, env):
        request_id, row = self._running(env)
        row["status"] = "CANCELLED"
        self._execution(env, "SUCCEEDED")
        assert h.body_of(_job_route(env, "GET", request_id))["status"] == "CANCELLED"

    @pytest.mark.parametrize("status", ["FAILED", "TIMED_OUT", "ABORTED"])
    def test_landed_archive_wins_over_a_failed_run(self, env, status):
        request_id, row = self._running(env)
        h.land_archive(env, row, size=99)
        self._execution(env, status)
        item = h.body_of(_job_route(env, "GET", request_id))
        assert item["status"] == "SUCCEEDED" and item["archiveSizeBytes"] == 99
        assert item["errorMessage"] is None and item.get("cancelledBy") is None

    def test_reconcile_aborts_the_open_upload_before_deciding(self, env):
        request_id, row = self._running(env)
        h.record_upload_token(row)
        env.s3.uploads.append({"Bucket": h.AUX, "Key": row["archiveKey"], "UploadId": "u1"})
        order = []
        abort, head = env.s3.abort_multipart_upload, env.s3.head_object
        env.s3.abort_multipart_upload = lambda **kw: (order.append("abort"), abort(**kw))[1]
        env.s3.head_object = lambda **kw: (order.append("head"), head(**kw))[1]
        self._execution(env, "FAILED")
        assert h.body_of(_job_route(env, "GET", request_id))["status"] == "FAILED"
        assert order[:2] == ["abort", "head"]


class TestEarlierSameSizeObject:
    """An earlier object of the archive's size at the key is not this job's archive."""

    def test_reconcile_of_a_failed_run_fails_the_job(self, env):
        request_id = h.body_of(_create(env, FILE_SELECTION))["requestId"]
        row = next(iter(env.zip.items.values()))
        row["status"] = "RUNNING"
        h.record_upload_token(row)
        env.s3.add(row["archiveKey"], size=99, bucket=h.AUX, version="old")
        env.executions.items[("exec-1", "GLOBAL:utility-zip-archive")] = {
            "workflowExecutionId": "exec-1", "workflowDatabaseId:workflowId": "GLOBAL:utility-zip-archive",
            "executionStatus": "FAILED", "executionError": ""}
        assert h.body_of(_job_route(env, "GET", request_id))["status"] == "FAILED"

    def test_cancel_cancels_the_job(self, env):
        request_id = h.body_of(_create(env, FILE_SELECTION))["requestId"]
        row = next(iter(env.zip.items.values()))
        h.record_upload_token(row)
        env.s3.add(row["archiveKey"], size=99, bucket=h.AUX, version="old")
        assert h.body_of(_job_route(env, "DELETE", request_id))["action"] == "cancelled"
        assert next(iter(env.zip.items.values()))["status"] == "CANCELLED"


class TestCancelLandedArchive:
    def test_cancel_aborts_the_open_upload_before_deciding(self, env):
        request_id = h.body_of(_create(env, FILE_SELECTION))["requestId"]
        row = next(iter(env.zip.items.values()))
        h.record_upload_token(row)
        env.s3.uploads.append({"Bucket": h.AUX, "Key": row["archiveKey"], "UploadId": "u1"})
        order = []
        abort, head = env.s3.abort_multipart_upload, env.s3.head_object
        env.s3.abort_multipart_upload = lambda **kw: (order.append("abort"), abort(**kw))[1]
        env.s3.head_object = lambda **kw: (order.append("head"), head(**kw))[1]
        assert h.body_of(_job_route(env, "DELETE", request_id))["action"] == "cancelled"
        assert order[:2] == ["abort", "head"]

    def test_archive_that_landed_before_the_cancel_wins(self, env):
        # The container completed its upload but had not recorded SUCCEEDED: the job is finished,
        # so the DELETE deletes it like any finished job instead of recording a second outcome.
        request_id = h.body_of(_create(env, FILE_SELECTION))["requestId"]
        row = next(iter(env.zip.items.values()))
        row["status"] = "RUNNING"
        h.land_archive(env, row, size=5)
        statuses = []
        update = env.zip.update_item

        def recording(**kw):
            result = update(**kw)
            statuses.append(env.zip.items[(kw["Key"]["userId"], kw["Key"]["requestId"])]["status"])
            return result
        env.zip.update_item = recording
        response = _job_route(env, "DELETE", request_id)
        assert h.body_of(response)["action"] == "deleted"
        assert statuses == ["SUCCEEDED"], "never CANCELLED over a landed archive"
        assert env.zip.items == {}
        # Only the create's launch: no abort of a run whose archive already landed.
        assert [c[0] for c in env.lam.calls] == ["executeFn"]


IDLE_STAMP = "2020-01-01T00:00:00Z"
MAIN_KEY = ("exec-1", "GLOBAL:utility-zip-archive")


class TestLostStatusEventBackstop:
    """A run that ended outside VAMS whose Step Functions status change event was lost: the main row
    still reads RUNNING, so the zip read drives the execution service's details read (which checks
    Step Functions and records the end) instead of leaving the job active until it expires."""

    def _idle_running(self, env, request_index=0):
        request_id = h.body_of(_create(env, FILE_SELECTION))["requestId"]
        row = env.zip.items[("alice", request_id)]
        row.update(status="RUNNING", workflowExecutionId="exec-1", updatedAt=IDLE_STAMP)
        env.executions.items[MAIN_KEY] = {
            "workflowExecutionId": "exec-1", "workflowDatabaseId:workflowId": "GLOBAL:utility-zip-archive",
            "executionStatus": "RUNNING", "executionError": ""}
        return request_id, row

    def _details_reads(self, env):
        return [c for c in env.lam.calls if c[0] == "executionServiceFn"
                and c[2]["requestContext"]["http"]["method"] == "GET"]

    def _stopped_outside_vams(self, env):
        # What the execution service's details read writes once Step Functions reports the stop.
        def effect(_event):
            env.executions.items[MAIN_KEY]["executionStatus"] = "ABORTED"
        env.lam.effects["executionServiceFn"] = effect

    def test_idle_job_is_finalized_from_the_details_read(self, env):
        request_id, _ = self._idle_running(env)
        self._stopped_outside_vams(env)
        item = h.body_of(_job_route(env, "GET", request_id))
        assert item["status"] == "FAILED"
        assert item["errorMessage"] == env.zd.MSG_CANCELLED_FROM_EXECUTIONS
        reads = self._details_reads(env)
        assert len(reads) == 1
        event = reads[0][2]
        assert event["requestContext"]["http"]["path"] == "/workflows/executions/exec-1/details"
        assert event["lambdaCrossCall"] == {"userName": "SYSTEM_USER"}

    def test_released_cap_slot(self, env):
        request_id, _ = self._idle_running(env)
        self._stopped_outside_vams(env)
        _list(env)
        assert env.zip.items[("alice", request_id)]["status"] == "FAILED"

    def test_active_job_is_not_checked(self, env):
        # The container wrote progress recently: its run is working, no check.
        request_id, row = self._idle_running(env)
        row["updatedAt"] = env.zd.iso(env.zd.utc_now())
        assert h.body_of(_job_route(env, "GET", request_id))["status"] == "RUNNING"
        assert self._details_reads(env) == []

    def test_still_running_run_stays_running_and_is_checked_once_per_interval(self, env):
        request_id, _ = self._idle_running(env)
        assert h.body_of(_job_route(env, "GET", request_id))["status"] == "RUNNING"
        assert h.body_of(_job_route(env, "GET", request_id))["status"] == "RUNNING"
        assert len(self._details_reads(env)) == 1, "the claim limits the check to one per interval"
        assert "runCheckedAt" in env.zip.items[("alice", request_id)]

    def test_check_is_due_again_after_the_interval(self, env):
        request_id, row = self._idle_running(env)
        row["runCheckedAt"] = IDLE_STAMP
        _job_route(env, "GET", request_id)
        assert len(self._details_reads(env)) == 1

    def test_failed_details_read_leaves_the_job_for_the_next_read(self, env):
        request_id, _ = self._idle_running(env)
        env.lam.responses["executionServiceFn"] = (403, {"message": "Not Authorized"})
        assert h.body_of(_job_route(env, "GET", request_id))["status"] == "RUNNING"

    def test_terminal_main_row_needs_no_check(self, env):
        request_id, _ = self._idle_running(env)
        env.executions.items[MAIN_KEY]["executionStatus"] = "FAILED"
        assert h.body_of(_job_route(env, "GET", request_id))["status"] == "FAILED"
        assert self._details_reads(env) == []

    def test_list_bounds_the_checks_per_call(self, env):
        env.svc.MAX_ACTIVE_PER_USER = 100
        for _ in range(env.svc.RUN_CHECKS_PER_CALL + 3):
            self._idle_running(env)
        _list(env, asset=False)
        assert len(self._details_reads(env)) == env.svc.RUN_CHECKS_PER_CALL

    def test_landed_archive_still_wins(self, env):
        request_id, row = self._idle_running(env)
        h.land_archive(env, row, size=7)
        self._stopped_outside_vams(env)
        item = h.body_of(_job_route(env, "GET", request_id))
        assert item["status"] == "SUCCEEDED" and item["archiveSizeBytes"] == 7

    def test_user_cancel_decides_without_waiting_for_an_event(self, env):
        # The cancel never defers to the run's status event: it records CANCELLED itself, then aborts.
        request_id, _ = self._idle_running(env)
        assert h.body_of(_job_route(env, "DELETE", request_id))["action"] == "cancelled"
        assert env.zip.items[("alice", request_id)]["status"] == "CANCELLED"
        assert self._details_reads(env) == []


class TestCapReconcilesBeforeRefusing:
    """At the per-user cap a create reconciles the caller's active jobs first, so a job whose run
    ended while every delivery of that end was lost (the terminal callback and the status change
    event) frees its slot on the next create, not only on the next list or open."""

    _backstop = TestLostStatusEventBackstop()

    def _idle_running(self, env):
        return self._backstop._idle_running(env)

    def _stopped_outside_vams(self, env):
        self._backstop._stopped_outside_vams(env)

    def _details_reads(self, env):
        return self._backstop._details_reads(env)

    def _fill(self, env, ended_status=None, idle_stopped=False):
        request_id, row = self._idle_running(env)
        if ended_status:
            env.executions.items[MAIN_KEY]["executionStatus"] = ended_status
        if idle_stopped:
            self._stopped_outside_vams(env)
        for _ in range(env.svc.MAX_ACTIVE_PER_USER - 1):
            assert _create(env, FILE_SELECTION)["statusCode"] == 200
        return request_id

    def test_an_ended_run_with_its_deliveries_lost_frees_the_slot(self, env):
        request_id = self._fill(env, ended_status="ABORTED")
        assert _create(env, FILE_SELECTION)["statusCode"] == 200
        row = env.zip.items[("alice", request_id)]
        assert row["status"] == "FAILED"
        assert row["errorMessage"] == env.zd.MSG_CANCELLED_FROM_EXECUTIONS

    def test_a_run_ended_outside_vams_with_its_event_lost_frees_the_slot(self, env):
        request_id = self._fill(env, idle_stopped=True)
        assert _create(env, FILE_SELECTION)["statusCode"] == 200
        assert env.zip.items[("alice", request_id)]["status"] == "FAILED"
        assert len(self._details_reads(env)) == 1

    def test_live_runs_still_hit_the_cap(self, env):
        request_id = self._fill(env)
        response = _create(env, FILE_SELECTION)
        assert response["statusCode"] == 429
        assert h.body_of(response)["message"] == env.zd.MSG_CAP_REACHED
        assert env.zip.items[("alice", request_id)]["status"] == "RUNNING"

    def test_below_the_cap_a_create_reconciles_nothing(self, env):
        request_id, _ = self._idle_running(env)
        env.executions.items[MAIN_KEY]["executionStatus"] = "ABORTED"
        assert _create(env, FILE_SELECTION)["statusCode"] == 200
        assert env.zip.items[("alice", request_id)]["status"] == "RUNNING"
        assert self._details_reads(env) == []

    def test_a_landed_archive_frees_the_slot_as_succeeded(self, env):
        request_id = self._fill(env, ended_status="ABORTED")
        h.land_archive(env, env.zip.items[("alice", request_id)], size=5)
        assert _create(env, FILE_SELECTION)["statusCode"] == 200
        assert env.zip.items[("alice", request_id)]["status"] == "SUCCEEDED"
