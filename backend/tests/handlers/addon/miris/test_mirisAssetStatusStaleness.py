# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""GET /addon/miris/asset-status `stale`: the file's dependency manifest, read from the auxiliary
bucket and checked against its SHA-256 id, compared with one listing of the asset's root prefix.
Every failure degrades to null and never fails the route."""

import hashlib
import io
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.addon.miris import dependencyManifest, getAssetStatus, mirisIntegration

from .conftest import (
    ASSET_ID,
    DATABASE_ID,
    STORED_UUID,
    FakeLambdaContext,
    asset_table as _asset_table,
    claims_after_normalizing,
    enforcer as _enforcer,
    metadata_table as _metadata_table,
    rest_event,
)

pytestmark = pytest.mark.unit

ROUTE_PATH = "/addon/miris/asset-status"
BINDING_TAG = f"vams-asset-{ASSET_ID}"
DATABASE_TAG = f"vams-database-{DATABASE_ID}"
ASSET_BUCKET = "vams-asset-bucket"
ROOT_KEY = f"{ASSET_ID}/"
_AS_USER = claims_after_normalizing({"tokens": ["user"], "roles": []})

TREE = {"scene.usda": "e-root", "layers/lookdev.usda": "e-layer", "textures/wood.png": "e-wood"}


def _manifest_bytes(files=TREE, root="scene.usda"):
    """The canonical bytes the upload container writes (dependency_manifest.canonical_bytes)."""
    manifest = {"root": root, "files": files}
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


MANIFEST = _manifest_bytes()
MANIFEST_ID = _sha(MANIFEST)


def _listing(files=TREE, root=ROOT_KEY, extra=None):
    """One listing page: each ETag quoted, as S3 returns it."""
    contents = [{"Key": f"{root}{rel}", "ETag": f'"{etag}"'} for rel, etag in files.items()]
    contents += [{"Key": key, "ETag": '"x"'} for key in (extra or [])]
    return [{"Contents": contents}]


class FakeS3:
    """Answers get_object from `objects` ({(bucket, key): bytes}) and list_objects_v2 from `pages`."""

    def __init__(self, objects=None, pages=None, get_error=None, list_error=None, paginate=None):
        self.objects = objects if objects is not None else {}
        self.pages = pages if pages is not None else []
        self.get_error, self.list_error = get_error, list_error
        self.get_calls, self.list_calls = [], []
        self._paginate = paginate

    def get_object(self, Bucket, Key):
        self.get_calls.append((Bucket, Key))
        if self.get_error:
            raise self.get_error
        data = self.objects.get((Bucket, Key))
        if data is None:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")
        return {"Body": io.BytesIO(data), "ContentLength": len(data)}

    def get_paginator(self, operation):
        assert operation == "list_objects_v2"
        paginator = MagicMock()

        def paginate(**kwargs):
            self.list_calls.append(kwargs)
            if self.list_error:
                raise self.list_error
            if self._paginate:
                return self._paginate(**kwargs)
            return iter(self.pages)

        paginator.paginate.side_effect = paginate
        return paginator


def _with_manifest(manifest_id=MANIFEST_ID, uuid=STORED_UUID):
    row = {
        "miris_asset_uuid": uuid,
        "miris_source_version_id": "v-abc",
        "miris_dependency_manifest": manifest_id,
    }
    return _metadata_table({f"{DATABASE_ID}:{ASSET_ID}:/scene.usda": {k: v for k, v in row.items() if v}})


def _buckets_table(registered=True):
    table = MagicMock()
    item = {"bucketId": "bucket-1", "bucketName": ASSET_BUCKET, "baseAssetsPrefix": "/",
            "bucketRegion": "us-east-1"}
    table.query.return_value = {"Items": [item] if registered else []}
    return table


def _stored_manifest(data=MANIFEST, manifest_id=MANIFEST_ID):
    key = dependencyManifest.manifest_key(DATABASE_ID, ASSET_ID, manifest_id)
    return {(getAssetStatus.auxiliary_bucket_name, key): data}


def _invoke(miris_api, s3, metadata_table=None, mode="perView", root_key=ROOT_KEY, buckets_table=None,
            context=None, binding_answer=None):
    binding = {"id": STORED_UUID, "state": "streamable", "tags": [BINDING_TAG, DATABASE_TAG]}
    if binding_answer is not None:
        miris_api.responses.append(binding_answer)
    elif mode == "static":
        miris_api.responses.extend([(200, {**binding, "state": "preview"}), (200, {"state": "streamable"})])
    else:
        miris_api.responses.append((200, binding))
    event = rest_event("GET", ROUTE_PATH, query={"databaseId": DATABASE_ID, "assetId": ASSET_ID,
                                                 "filePath": "/scene.usda"})
    with patch.object(getAssetStatus, "asset_file_metadata_table", metadata_table or _with_manifest()), \
         patch.object(getAssetStatus, "asset_table", _asset_table(root_key=root_key)), \
         patch.object(getAssetStatus, "buckets_table", buckets_table or _buckets_table()), \
         patch.object(getAssetStatus, "s3", s3), \
         patch.object(getAssetStatus, "CasbinEnforcer", return_value=_enforcer()), \
         patch.object(getAssetStatus, "request_to_claims", side_effect=_AS_USER), \
         patch.object(getAssetStatus, "miris_viewer_key", "viewer-key" if mode == "static" else ""), \
         patch.object(getAssetStatus, "miris_viewer_key_mode", mode):
        response = getAssetStatus.lambda_handler(event, context or FakeLambdaContext())
    assert response["statusCode"] == 200
    return json.loads(response["body"])


class TestStale:
    def test_all_files_match_is_not_stale(self, miris_api):
        s3 = FakeS3(objects=_stored_manifest(), pages=_listing(extra=[f"{ROOT_KEY}unrelated.txt", ROOT_KEY]))
        body = _invoke(miris_api, s3)
        assert (body["state"], body["stale"]) == ("streamable", False)
        assert s3.get_calls == [(getAssetStatus.auxiliary_bucket_name,
                                 f"pipelines/miris-upload/manifests/{DATABASE_ID}/{ASSET_ID}/{MANIFEST_ID}.json")]
        [listing] = s3.list_calls
        assert (listing["Bucket"], listing["Prefix"]) == (ASSET_BUCKET, ROOT_KEY)

    @pytest.mark.parametrize("changed", ["textures/wood.png", "layers/lookdev.usda", "scene.usda"],
                             ids=["texture", "sublayer", "root"])
    def test_a_changed_etag_is_stale(self, miris_api, changed):
        s3 = FakeS3(objects=_stored_manifest(), pages=_listing({**TREE, changed: "e-new"}))
        assert _invoke(miris_api, s3)["stale"] is True

    @pytest.mark.parametrize("removed", ["textures/wood.png", "layers/lookdev.usda"])
    def test_a_removed_file_is_stale(self, miris_api, removed):
        files = {rel: etag for rel, etag in TREE.items() if rel != removed}
        s3 = FakeS3(objects=_stored_manifest(), pages=_listing(files))
        assert _invoke(miris_api, s3)["stale"] is True

    def test_a_file_split_across_pages_matches(self, miris_api):
        [page] = _listing()
        pages = [{"Contents": [entry]} for entry in page["Contents"]]
        s3 = FakeS3(objects=_stored_manifest(), pages=pages)
        assert _invoke(miris_api, s3)["stale"] is False

    @pytest.mark.parametrize("root_key", ["/asset-1", "asset-1"])
    def test_the_root_key_is_normalized(self, miris_api, root_key):
        s3 = FakeS3(objects=_stored_manifest(), pages=_listing())
        assert _invoke(miris_api, s3, root_key=root_key)["stale"] is False
        assert s3.list_calls[0]["Prefix"] == ROOT_KEY

    @pytest.mark.parametrize("mode", ["static", "perView"])
    def test_staleness_is_reported_in_both_modes(self, miris_api, mode):
        s3 = FakeS3(objects=_stored_manifest(), pages=_listing({**TREE, "scene.usda": "e-new"}))
        body = _invoke(miris_api, s3, mode=mode)
        assert (body["state"], body["stale"]) == ("streamable", True)


class TestUnknown:
    def test_no_manifest_field_is_null_without_any_read(self, miris_api):
        s3 = FakeS3()
        body = _invoke(miris_api, s3, metadata_table=_with_manifest(manifest_id=None))
        assert (body["state"], body["stale"]) == ("streamable", None)
        assert (s3.get_calls, s3.list_calls) == ([], [])

    def test_an_unusable_uuid_is_null_without_any_read(self, miris_api):
        s3 = FakeS3(objects=_stored_manifest(), pages=_listing())
        event = rest_event("GET", ROUTE_PATH, query={"databaseId": DATABASE_ID, "assetId": ASSET_ID,
                                                     "filePath": "/scene.usda"})
        with patch.object(getAssetStatus, "asset_file_metadata_table", _with_manifest(uuid="not-a-uuid")), \
             patch.object(getAssetStatus, "asset_table", _asset_table(root_key=ROOT_KEY)), \
             patch.object(getAssetStatus, "s3", s3), \
             patch.object(getAssetStatus, "CasbinEnforcer", return_value=_enforcer()), \
             patch.object(getAssetStatus, "request_to_claims", side_effect=_AS_USER):
            body = json.loads(getAssetStatus.lambda_handler(event, FakeLambdaContext())["body"])
        assert (body["state"], body["stale"]) == ("invalidUuid", None)
        assert (s3.get_calls, s3.list_calls) == ([], [])

    @pytest.mark.parametrize("manifest_id", ["abc", MANIFEST_ID.upper(), "../" + MANIFEST_ID[3:]],
                             ids=["short", "uppercase", "path"])
    def test_a_malformed_manifest_id_is_null_without_any_read(self, miris_api, manifest_id):
        s3 = FakeS3(objects=_stored_manifest(), pages=_listing())
        body = _invoke(miris_api, s3, metadata_table=_with_manifest(manifest_id=manifest_id))
        assert body["stale"] is None
        assert (s3.get_calls, s3.list_calls) == ([], [])

    def test_a_missing_manifest_is_null(self, miris_api, log_recorder):
        s3 = FakeS3(objects={}, pages=_listing())
        body = _invoke(miris_api, s3)
        assert (body["state"], body["stale"]) == ("streamable", None)
        assert s3.list_calls == []
        assert "is stale" in log_recorder.text()

    def test_an_unreadable_manifest_is_null(self, miris_api):
        s3 = FakeS3(get_error=ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "GetObject"),
                    pages=_listing())
        assert _invoke(miris_api, s3)["stale"] is None

    def test_a_manifest_that_does_not_hash_to_its_id_is_null(self, miris_api):
        tampered = _manifest_bytes({**TREE, "scene.usda": "e-other"})
        s3 = FakeS3(objects=_stored_manifest(data=tampered), pages=_listing({**TREE, "scene.usda": "e-other"}))
        assert _invoke(miris_api, s3)["stale"] is None
        assert s3.list_calls == []

    @pytest.mark.parametrize("data", [b"not json", b"[]", b'{"files":{}}', b'{"files":{"a":1}}', b'{"files":["a"]}'],
                             ids=["not-json", "not-object", "no-files", "non-string-etag", "files-not-object"])
    def test_a_malformed_manifest_is_null(self, miris_api, data):
        s3 = FakeS3(objects=_stored_manifest(data=data, manifest_id=_sha(data)), pages=_listing())
        body = _invoke(miris_api, s3, metadata_table=_with_manifest(manifest_id=_sha(data)))
        assert body["stale"] is None

    def test_a_listing_error_is_null(self, miris_api, log_recorder):
        s3 = FakeS3(objects=_stored_manifest(),
                    list_error=ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "ListObjectsV2"))
        body = _invoke(miris_api, s3)
        assert (body["state"], body["stale"]) == ("streamable", None)
        assert "AccessDenied" in log_recorder.text()

    def test_an_unregistered_bucket_is_null(self, miris_api):
        s3 = FakeS3(objects=_stored_manifest(), pages=_listing())
        assert _invoke(miris_api, s3, buckets_table=_buckets_table(registered=False))["stale"] is None
        assert s3.list_calls == []

    def test_an_asset_without_a_root_key_is_null(self, miris_api):
        s3 = FakeS3(objects=_stored_manifest(), pages=_listing())
        assert _invoke(miris_api, s3, root_key=None)["stale"] is None
        assert s3.list_calls == []


class TestSkipped:
    @pytest.mark.parametrize("binding_answer, state", [
        (404, "notBound"),
        ((200, {"id": STORED_UUID, "state": "streamable", "tags": ["vams"]}), "notBound"),
        ((200, {"id": STORED_UUID, "state": "deleted", "tags": [BINDING_TAG, DATABASE_TAG]}), "notBound"),
        ((500, "boom"), "unknown"),
    ], ids=["missing", "unbound", "deleted", "unreadable"])
    def test_a_not_bound_or_unknown_stream_is_not_compared(self, miris_api, binding_answer, state):
        s3 = FakeS3(objects=_stored_manifest(), pages=_listing())
        buckets = _buckets_table()
        body = _invoke(miris_api, s3, buckets_table=buckets, binding_answer=binding_answer)
        assert (body["state"], body["stale"]) == (state, None)
        assert (s3.get_calls, s3.list_calls) == ([], [])
        buckets.query.assert_not_called()


class TestBoundedCalls:
    def test_every_staleness_call_has_short_timeouts_and_two_attempts(self):
        config = getAssetStatus.staleness_call_config
        assert (config.connect_timeout, config.read_timeout) == (1, 2)
        assert config.retries == {"total_max_attempts": 2, "mode": "standard"}
        assert getAssetStatus.s3._config_override is config
        # One call's worst case: two attempts of connect plus read, and one backoff of at most 1 s.
        assert getAssetStatus._STALENESS_MIN_SECONDS == 7

    def test_the_handler_starts_no_thread(self):
        import inspect
        source = inspect.getsource(getAssetStatus)
        assert "threading" not in source and "ThreadPoolExecutor" not in source


class TestDeadline:
    def test_too_little_time_left_is_null_without_any_read(self, miris_api, monkeypatch):
        # Enough for the Miris call, less than one staleness call's worst case.
        left = getAssetStatus._STALENESS_MIN_SECONDS - 0.5
        monkeypatch.setattr(mirisIntegration, "request_deadline",
                            lambda context, event=None: time.monotonic() + left)
        # The Miris read needs more time than this leaves; its outcome is covered elsewhere.
        monkeypatch.setattr(getAssetStatus, "_stream_state", lambda *args: "streamable")
        s3 = FakeS3(objects=_stored_manifest(), pages=_listing())
        buckets = _buckets_table()
        body = _invoke(miris_api, s3, buckets_table=buckets)
        assert (body["state"], body["stale"]) == ("streamable", None)
        assert (s3.get_calls, s3.list_calls) == ([], [])
        buckets.query.assert_not_called()

    def test_the_listing_stops_between_pages_at_the_deadline(self, miris_api, monkeypatch):
        left = getAssetStatus._STALENESS_MIN_SECONDS + 0.6
        monkeypatch.setattr(mirisIntegration, "request_deadline",
                            lambda context, event=None: time.monotonic() + left)
        # The Miris read needs more time than this leaves; its outcome is covered elsewhere.
        monkeypatch.setattr(getAssetStatus, "_stream_state", lambda *args: "streamable")
        [page] = _listing()
        pages_read = []

        def slow_pages(**_kwargs):
            for _ in range(1000):
                time.sleep(0.2)
                pages_read.append(1)
                yield page

        s3 = FakeS3(objects=_stored_manifest(), paginate=slow_pages)
        started = time.monotonic()
        body = _invoke(miris_api, s3)
        assert body["stale"] is None
        # The manifest was read and the listing begun, then stopped after a few pages.
        assert len(s3.get_calls) == 1 and len(s3.list_calls) == 1
        assert 1 <= len(pages_read) <= 5
        assert time.monotonic() - started < 2
