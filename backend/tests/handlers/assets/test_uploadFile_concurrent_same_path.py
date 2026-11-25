# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Concurrent uploads of one file path each complete (#1928).

Every upload of a path staged its file at the one key ``{baseAssetsPrefix}temp-uploads/{final key}``.
Two completions running together each completed their multipart upload over that key, so each read
the other's file back, answered "Upload ID mismatch." and deleted it: the losing completion removed
the file the winning one staged, and often both failed. Now each upload stages in its own folder,
``temp-uploads/{upload tag}/{final key}``, so the uploads never share a staged object, and each lands
one version of the file.

These tests drive the real initialize_upload and complete_upload against moto S3 (a versioned bucket)
and a moto upload table; only the final copy into the asset is replaced by its effect on moto
(`_copy_on`). Parts are sent to the presigned URLs the initialization returned, as the web app and the
CLI send them. moto answers ListParts, AbortMultipartUpload and CompleteMultipartUpload for
an upload id under ANY key; S3 binds an upload id to the key it was created under and answers
NoSuchUpload for another key, so the client is wrapped to do the same (`_KeyBoundS3`).

The concurrency tests run two completions in threads with their S3 calls in a fixed order
(`_Interleaved`): both multipart uploads complete before either completion reads its staged object, and
both read it before either goes on. That is the interleaving the burst produced on a deployment; fixed,
it fails on the per-path layout every run instead of most runs.

An upload initialized before the upgrade staged at the per-path key and still completes, or is
aborted, there (the LEGACY STAGING KEY fallback).
"""

import os
import threading
import time
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import boto3
import pytest
import requests
from botocore.config import Config
from botocore.exceptions import ClientError
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import uploadFile as uf  # noqa: E402

REGION = "us-east-1"
BUCKET = "asset-bucket"
TABLE = "t-asset-uploads-1928"
DATABASE_ID = "db-1"
ASSET_ID = "asset-1"
ASSET = {"databaseId": DATABASE_ID, "assetId": ASSET_ID, "bucketId": "bucket-1",
         "assetLocation": {"Key": f"{ASSET_ID}/"}}
CLAIMS = {"tokens": ["alice@corp"], "roles": []}
RELATIVE_KEY = "/scans/pump.glb"
FINAL_KEY = "asset-1/scans/pump.glb"
STAGING_PREFIX = "temp-uploads/"
LEGACY_STAGING_KEY = STAGING_PREFIX + FINAL_KEY
MISMATCH = "Upload ID mismatch."
WAIT_SECONDS = 20


class _KeyBoundS3:
    """The moto S3 client, with S3's binding of a multipart upload id to the key it was created under.

    A multipart call for an upload id this client created, sent for another key, answers NoSuchUpload
    as S3 does. Every other call goes to moto unchanged. `puts` records the keys of PutObject calls."""

    _BOUND = ("list_parts", "abort_multipart_upload", "complete_multipart_upload", "upload_part")

    def __init__(self, client):
        self._client = client
        self._keys = {}
        self.puts = []

    def create_multipart_upload(self, **kwargs):
        response = self._client.create_multipart_upload(**kwargs)
        self._keys[response["UploadId"]] = kwargs["Key"]
        return response

    def put_object(self, **kwargs):
        self.puts.append(kwargs["Key"])
        return self._client.put_object(**kwargs)

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if name not in self._BOUND:
            return attr

        def bound(**kwargs):
            key = self._keys.get(kwargs.get("UploadId"))
            if key is not None and key != kwargs.get("Key"):
                raise ClientError({"Error": {"Code": "NoSuchUpload",
                                             "Message": "The specified upload does not exist."}}, name)
            return attr(**kwargs)
        return bound


class _Interleaved:
    """Runs completions in threads with their S3 calls in a fixed order.

    The completions' CompleteMultipartUpload calls run one at a time in `order`; no completion reads its
    staged object (its first HeadObject after that call) until every one has completed its multipart
    upload, and none goes on until every one has read it."""

    def __init__(self, s3, order):
        self._s3 = s3
        self._order = list(order)
        self._turn = threading.Condition()
        self._next = 0
        self._completed = threading.Barrier(len(order), timeout=WAIT_SECONDS)
        self._read = threading.Barrier(len(order), timeout=WAIT_SECONDS)
        self._local = threading.local()

    def run_as(self, role, fn):
        self._local.role = role
        return fn()

    def __getattr__(self, name):
        return getattr(self._s3, name)

    def complete_multipart_upload(self, **kwargs):
        role = getattr(self._local, "role", None)
        with self._turn:
            assert self._turn.wait_for(lambda: self._order[self._next] == role, timeout=WAIT_SECONDS)
            try:
                response = self._s3.complete_multipart_upload(**kwargs)
            finally:
                self._next += 1
                self._turn.notify_all()
        self._local.completed = True
        self._completed.wait()
        return response

    def head_object(self, **kwargs):
        response = self._s3.head_object(**kwargs)
        if getattr(self._local, "completed", False) and not getattr(self._local, "read", False):
            self._local.read = True
            self._read.wait()
        return response


def _copy_on(client):
    """The final copy's effect, on moto: a new version of the destination with the body of the staged
    object's current version, or False when the staged object is gone. These tests are about where a
    file is staged; how the copy itself is made has its own tests."""
    def copy(source_bucket, source_key, dest_bucket, dest_key, database_id, asset_id, extra_metadata=None,
             source_head=None):
        try:
            client.copy_object(CopySource={"Bucket": source_bucket, "Key": source_key}, Bucket=dest_bucket,
                               Key=dest_key, MetadataDirective="REPLACE",
                               Metadata={"databaseid": database_id, "assetid": asset_id})
        except ClientError:
            return False
        return True
    return copy


@pytest.fixture
def env():
    with mock_aws(), ExitStack() as stack:
        client = boto3.client("s3", region_name=REGION,
                              config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        table = boto3.resource("dynamodb", region_name=REGION).create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "uploadId", "KeyType": "HASH"},
                       {"AttributeName": "assetId", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "uploadId", "AttributeType": "S"},
                                  {"AttributeName": "assetId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST")
        s3 = _KeyBoundS3(client)
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))  # noqa: E731
        p(uf, "s3", s3)
        p(uf, "copy_s3_object", side_effect=_copy_on(client))
        p(uf, "asset_upload_table", table)
        p(uf, "claims_and_roles", CLAIMS, create=True)
        p(uf, "check_user_rate_limit", return_value=True)
        p(uf, "get_asset_details", return_value=dict(ASSET))
        p(uf, "get_database_details", return_value={"databaseId": DATABASE_ID})
        p(uf, "get_default_bucket_details", return_value={
            "bucketId": "bucket-1", "bucketName": BUCKET, "baseAssetsPrefix": "", "bucketRegion": REGION})
        # The name blocklist, stubbed to refuse .exe (the module is replaced by a mock in the suite).
        p(uf, "validateUnallowedFileExtensionAndContentType",
          side_effect=lambda key, content_type: not key.lower().endswith(".exe"))
        p(uf, "validateS3AssetExtensionsAndContentType", return_value=True)
        p(uf, "determine_asset_type", return_value="folder")
        p(uf, "update_asset_attributes")
        p(uf, "send_subscription_email")
        p(uf, "log_file_upload")
        yield {"client": client, "s3": s3, "table": table}


def _event(upload_id):
    return {"requestContext": {"http": {"method": "POST", "path": f"/uploads/{upload_id}/complete"}},
            "pathParameters": None, "queryStringParameters": None,
            "headers": {"authorization": "Bearer test-token"}, "body": None}


def _initialize(*relative_keys, size=16):
    from backend.backend.models.assetsV3 import InitializeUploadRequestModel
    return uf.initialize_upload(InitializeUploadRequestModel(
        assetId=ASSET_ID, databaseId=DATABASE_ID, uploadType="assetFile",
        files=[{"relativeKey": key, "file_size": size} for key in relative_keys]), CLAIMS)


def _send_parts(file_response, body):
    """PUT the body to each presigned part URL, as the clients do; the completion entry for the file."""
    parts = []
    for part in file_response.partUploadUrls:
        answer = requests.put(part.UploadUrl, data=body)
        assert answer.status_code == 200, answer.text
        parts.append({"PartNumber": part.PartNumber, "ETag": answer.headers["ETag"]})
    assert parts, "the initialization returned no part URL"
    return {"relativeKey": file_response.relativeKey, "uploadIdS3": file_response.uploadIdS3, "parts": parts}


def _start(body, relative_key=RELATIVE_KEY):
    """Initialize one upload of `relative_key` and send its parts: (uploadId, completion file entry)."""
    init = _initialize(relative_key, size=len(body))
    return init.uploadId, _send_parts(init.files[0], body)


def _complete(upload_id, *files):
    from backend.backend.models.assetsV3 import CompleteUploadRequestModel
    return uf.complete_upload(upload_id, CompleteUploadRequestModel(
        assetId=ASSET_ID, databaseId=DATABASE_ID, uploadType="assetFile", files=list(files)), _event(upload_id))


def _results(response):
    """{relativeKey: (success, error)} of a completion, whether it answered 200 or 409."""
    if isinstance(response, dict):
        import json
        results = json.loads(response["body"])["fileResults"]
        return {r["relativeKey"]: (r["success"], r.get("error")) for r in results}
    return {r.relativeKey: (r.success, r.error) for r in response.fileResults}


def _complete_together(env, order, jobs):
    """Run the completions of `jobs` ({role: (uploadId, file entry)}) in threads, interleaved."""
    interleaved = _Interleaved(env["s3"], order)
    results, errors = {}, {}

    def run(role, upload_id, file_entry):
        try:
            results[role] = interleaved.run_as(role, lambda: _complete(upload_id, file_entry))
        except BaseException as error:  # reported below, so a thread's failure fails the test
            errors[role] = error

    threads = [threading.Thread(target=run, args=(role, upload_id, entry))
               for role, (upload_id, entry) in jobs.items()]
    with patch.object(uf, "s3", interleaved):
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
    assert not [t for t in threads if t.is_alive()], "a completion did not finish"
    assert not errors, errors
    return {role: _results(response) for role, response in results.items()}


def _bodies_of_versions(env, key):
    """The bodies of every version of `key`, oldest first (ListObjectVersions lists a key's versions
    newest first)."""
    listed = env["client"].list_object_versions(Bucket=BUCKET, Prefix=key).get("Versions", [])
    versions = [v for v in listed if v["Key"] == key][::-1]
    return [env["client"].get_object(Bucket=BUCKET, Key=key, VersionId=v["VersionId"])["Body"].read()
            for v in versions]


def _current_body(env, key):
    return env["client"].get_object(Bucket=BUCKET, Key=key)["Body"].read()


def _staged_versions(env):
    """Every version and delete marker left under the staging prefix."""
    listed = env["client"].list_object_versions(Bucket=BUCKET, Prefix=STAGING_PREFIX)
    return [(v["Key"], v["VersionId"]) for v in listed.get("Versions", []) + listed.get("DeleteMarkers", [])]


def _open_uploads(env):
    return [(u["Key"], u["UploadId"]) for u in env["client"].list_multipart_uploads(Bucket=BUCKET).get("Uploads", [])]


def _staging_key_of(env, upload_id_s3):
    keys = [key for key, upload in _open_uploads(env) if upload == upload_id_s3]
    assert len(keys) == 1, keys
    return keys[0]


@pytest.mark.unit
class TestEachUploadStagesInItsOwnFolder:
    def test_two_uploads_of_one_path_stage_under_different_keys(self, env):
        first, second = _initialize(RELATIVE_KEY), _initialize(RELATIVE_KEY)
        first_key = _staging_key_of(env, first.files[0].uploadIdS3)
        second_key = _staging_key_of(env, second.files[0].uploadIdS3)

        assert first_key != second_key
        for key in (first_key, second_key):
            assert key.startswith(STAGING_PREFIX) and key.endswith("/" + FINAL_KEY)

    def test_the_staging_key_is_the_documented_layout(self, env):
        """temp-uploads/{12 hex characters of the SHA-256 of the upload id}/{final key}, for every file of
        the upload."""
        import hashlib
        init = _initialize(RELATIVE_KEY, "/scans/pump.e57")
        tag = hashlib.sha256(init.uploadId.encode("utf-8")).hexdigest()[:12]

        assert sorted(key for key, _ in _open_uploads(env)) == sorted([
            f"{STAGING_PREFIX}{tag}/asset-1/scans/pump.e57", f"{STAGING_PREFIX}{tag}/{FINAL_KEY}"])

    def test_a_zero_byte_file_is_staged_in_the_upload_folder(self, env):
        init = _initialize(RELATIVE_KEY, size=0)
        assert init.files[0].uploadIdS3 == "zero-byte"

        response = _complete(init.uploadId, {"relativeKey": RELATIVE_KEY, "uploadIdS3": "zero-byte", "parts": []})

        assert _results(response) == {RELATIVE_KEY: (True, None)}
        assert env["s3"].puts, "the zero-byte file was not staged"
        assert all(key != LEGACY_STAGING_KEY and key.startswith(STAGING_PREFIX) and key.endswith("/" + FINAL_KEY)
                   for key in env["s3"].puts)
        assert _bodies_of_versions(env, FINAL_KEY) == [b""]
        assert _staged_versions(env) == []


@pytest.mark.unit
class TestConcurrentUploadsOfOnePathEachLand:
    @pytest.mark.parametrize("order", [("A", "B"), ("B", "A")])
    def test_completed_one_after_the_other_both_land(self, env, order):
        """Control: completions that do not overlap succeeded on the per-path layout too."""
        started = {"A": _start(b"A" * 16), "B": _start(b"B" * 16)}

        for role in order:
            upload_id, entry = started[role]
            assert _results(_complete(upload_id, entry)) == {RELATIVE_KEY: (True, None)}

        assert sorted(_bodies_of_versions(env, FINAL_KEY)) == [b"A" * 16, b"B" * 16]
        assert _current_body(env, FINAL_KEY) == order[-1].encode() * 16
        assert _staged_versions(env) == []
        assert _open_uploads(env) == []

    @pytest.mark.parametrize("order", [("A", "B"), ("B", "A")])
    def test_completed_together_both_land(self, env, order):
        """Both multipart uploads complete before either completion reads its staged object. On the
        per-path layout each then read the other's file, answered "Upload ID mismatch." and deleted it."""
        started = {"A": _start(b"A" * 16), "B": _start(b"B" * 16)}

        results = _complete_together(env, order, started)

        assert results == {"A": {RELATIVE_KEY: (True, None)}, "B": {RELATIVE_KEY: (True, None)}}
        assert sorted(_bodies_of_versions(env, FINAL_KEY)) == [b"A" * 16, b"B" * 16]
        assert _staged_versions(env) == []
        assert _open_uploads(env) == []


@pytest.mark.unit
class TestAStagedObjectOfAnotherUploadIsRefused:
    def test_only_the_refused_upload_loses_its_staged_object(self, env):
        """The tamper guard stays: an upload whose staged object carries another upload id is refused
        and its staged object deleted. An upload of the same path completing at the same time lands."""
        victim_id, victim = _start(b"B" * 16)
        upload_id, entry = _start(b"A" * 16)
        # Replace the refused upload's multipart upload with one that carries another upload id, under
        # the key the initialization staged it at.
        key = _staging_key_of(env, entry["uploadIdS3"])
        env["client"].abort_multipart_upload(Bucket=BUCKET, Key=key, UploadId=entry["uploadIdS3"])
        intruder = env["s3"].create_multipart_upload(
            Bucket=BUCKET, Key=key, Metadata={"databaseid": DATABASE_ID, "assetid": ASSET_ID,
                                              "uploadid": "y-another-upload"})["UploadId"]
        etag = env["s3"].upload_part(Bucket=BUCKET, Key=key, UploadId=intruder, PartNumber=1,
                                     Body=b"X" * 16)["ETag"]
        tampered = {"relativeKey": RELATIVE_KEY, "uploadIdS3": intruder,
                    "parts": [{"PartNumber": 1, "ETag": etag}]}

        results = _complete_together(env, ("B", "A"), {"A": (upload_id, tampered), "B": (victim_id, victim)})

        assert results == {"A": {RELATIVE_KEY: (False, MISMATCH)}, "B": {RELATIVE_KEY: (True, None)}}
        assert _bodies_of_versions(env, FINAL_KEY) == [b"B" * 16]
        assert _staged_versions(env) == []


def _legacy_start(env, body, relative_key=RELATIVE_KEY):
    """An upload as the initialization before per-upload staging left it: the upload record, and a
    multipart upload under the per-path staging key with the body's one part sent."""
    from backend.backend.models.assetsV3 import AssetUploadTableModel
    upload_id = "y00000000-1928-4000-8000-legacy000001"
    key = STAGING_PREFIX + "asset-1/" + relative_key.lstrip("/")
    upload_id_s3 = env["s3"].create_multipart_upload(
        Bucket=BUCKET, Key=key, ContentType="application/octet-stream",
        Metadata={"databaseid": DATABASE_ID, "assetid": ASSET_ID, "uploadid": upload_id})["UploadId"]
    etag = env["s3"].upload_part(Bucket=BUCKET, Key=key, UploadId=upload_id_s3, PartNumber=1, Body=body)["ETag"]
    env["table"].put_item(Item=AssetUploadTableModel(
        uploadId=upload_id, assetId=ASSET_ID, databaseId=DATABASE_ID, uploadType="assetFile",
        createdAt="2026-11-25T00:00:00+00:00", expiresAt=int(time.time()) + 3600, totalFiles=1, totalParts=1,
        status="initialized", UserId="alice@corp", files=[relative_key]).to_dict())
    return upload_id, {"relativeKey": relative_key, "uploadIdS3": upload_id_s3,
                       "parts": [{"PartNumber": 1, "ETag": etag}]}


@pytest.mark.unit
class TestAnUploadInitializedBeforePerUploadStaging:
    def test_it_completes_under_its_per_path_key(self, env):
        upload_id, entry = _legacy_start(env, b"L" * 16)

        assert _results(_complete(upload_id, entry)) == {RELATIVE_KEY: (True, None)}
        assert _bodies_of_versions(env, FINAL_KEY) == [b"L" * 16]
        assert _staged_versions(env) == []
        assert _open_uploads(env) == []

    def test_it_lands_beside_an_upload_of_the_same_path_initialized_after(self, env):
        legacy_id, legacy = _legacy_start(env, b"L" * 16)
        upload_id, entry = _start(b"N" * 16)

        assert _results(_complete(legacy_id, legacy)) == {RELATIVE_KEY: (True, None)}
        assert _results(_complete(upload_id, entry)) == {RELATIVE_KEY: (True, None)}
        assert _bodies_of_versions(env, FINAL_KEY) == [b"L" * 16, b"N" * 16]
        assert _staged_versions(env) == []

    def test_a_cancelled_one_is_aborted_under_its_per_path_key(self, env):
        upload_id, entry = _legacy_start(env, b"L" * 16)

        response = _complete(upload_id, {**entry, "parts": []})

        assert _results(response)[RELATIVE_KEY][0] is False
        assert _open_uploads(env) == []
        assert _bodies_of_versions(env, FINAL_KEY) == []

    def test_one_refused_by_its_name_is_aborted_under_its_per_path_key(self, env):
        upload_id, entry = _legacy_start(env, b"L" * 16, relative_key="/scans/payload.exe")

        response = _complete(upload_id, entry)

        assert _results(response)["/scans/payload.exe"][0] is False
        assert _open_uploads(env) == []

    def test_a_large_one_is_queued_with_its_per_path_key(self, env):
        upload_id, entry = _legacy_start(env, b"L" * 16)
        queue = MagicMock(return_value=True)
        with patch.object(uf, "should_process_asynchronously", return_value=True), \
                patch.object(uf, "queue_large_file_for_processing", queue):
            response = _complete(upload_id, entry)

        assert _results(response) == {RELATIVE_KEY: (True, None)}
        queue.assert_called_once()
        assert queue.call_args.args[0]["tempS3Key"] == LEGACY_STAGING_KEY


def _error_lines(logger):
    """The ERROR-level calls made on a mocked module logger."""
    return logger.error.call_args_list + logger.exception.call_args_list


def _error_lines_with_traceback(logger):
    """The ERROR-level calls that carry a traceback: `logger.exception`, or `logger.error` with `exc_info`."""
    return logger.exception.call_args_list + [c for c in logger.error.call_args_list if c.kwargs.get("exc_info")]


class _FirstReadRefused:
    """The S3 client, with ListParts under `key` refused by `code` for every upload id."""

    def __init__(self, s3, key, code):
        self._s3, self._key, self._code = s3, key, code

    def __getattr__(self, name):
        return getattr(self._s3, name)

    def list_parts(self, **kwargs):
        if kwargs.get("Key") == self._key:
            raise ClientError({"Error": {"Code": self._code, "Message": self._code}}, "ListParts")
        return self._s3.list_parts(**kwargs)


@pytest.mark.unit
class TestTheStagedPartsReadLogsAnErrorOnlyWhenItStands:
    """#1971: completing an upload initialized before per-upload staging first reads its parts under the
    per-upload key, which answers NoSuchUpload by design. That read logged an ERROR line with a stack
    trace for every such upload; it is logged only when the upload cannot be completed."""

    def test_an_upload_initialized_before_per_upload_staging_completes_with_no_error_line(self, env):
        upload_id, entry = _legacy_start(env, b"L" * 16)

        with patch.object(uf, "logger") as logger:
            assert _results(_complete(upload_id, entry)) == {RELATIVE_KEY: (True, None)}

        assert _error_lines(logger) == []
        assert logger.warning.call_args_list == []
        assert any("initialized before per-upload staging" in str(c) for c in logger.info.call_args_list)

    def test_an_upload_initialized_after_completes_with_no_error_line(self, env):
        upload_id, entry = _start(b"N" * 16)

        with patch.object(uf, "logger") as logger:
            assert _results(_complete(upload_id, entry)) == {RELATIVE_KEY: (True, None)}

        assert _error_lines(logger) == []

    def test_an_upload_open_under_neither_key_logs_its_error_once(self, env):
        temp_key = uf.staged_upload_key("", "y00000000-1971-4000-8000-gone00000001", FINAL_KEY)

        with patch.object(uf, "logger") as logger:
            located = uf.locate_staged_multipart_upload(BUCKET, temp_key, LEGACY_STAGING_KEY, "no-such-upload",
                                                        [MagicMock(PartNumber=1)])

        key, size, success, error = located
        assert (key, size, success) == (temp_key, 0, False)
        assert error.startswith("Error verifying upload parts: ") and "NoSuchUpload" in error
        assert len(_error_lines(logger)) == 1
        assert len(_error_lines_with_traceback(logger)) == 1

    def test_another_first_read_error_that_the_legacy_key_recovers_is_a_warning(self, env):
        upload_id, entry = _legacy_start(env, b"L" * 16)
        temp_key = uf.staged_upload_key("", upload_id, FINAL_KEY)

        with patch.object(uf, "s3", _FirstReadRefused(env["s3"], temp_key, "SlowDown")), \
                patch.object(uf, "logger") as logger:
            assert _results(_complete(upload_id, entry)) == {RELATIVE_KEY: (True, None)}

        assert _error_lines(logger) == []
        assert len(logger.warning.call_args_list) == 1
        assert "SlowDown" in logger.warning.call_args.args[0]

    def test_a_first_read_error_with_no_legacy_upload_is_logged_and_returned(self, env):
        upload_id, entry = _start(b"N" * 16)
        temp_key = uf.staged_upload_key("", upload_id, FINAL_KEY)

        with patch.object(uf, "s3", _FirstReadRefused(env["s3"], temp_key, "AccessDenied")), \
                patch.object(uf, "logger") as logger:
            results = _results(_complete(upload_id, entry))

        success, error = results[RELATIVE_KEY]
        assert success is False and "AccessDenied" in error
        assert len(_error_lines(logger)) == 1
        assert len(_error_lines_with_traceback(logger)) == 1


@pytest.mark.unit
class TestAbortsReachTheRightUpload:
    def test_a_cancelled_upload_aborts_only_its_own_multipart_upload(self, env):
        cancelled_id, cancelled = _start(b"A" * 16)
        upload_id, entry = _start(b"B" * 16)

        response = _complete(cancelled_id, {**cancelled, "parts": []})

        assert _results(response)[RELATIVE_KEY][0] is False
        assert [upload for _, upload in _open_uploads(env)] == [entry["uploadIdS3"]]
        assert _results(_complete(upload_id, entry)) == {RELATIVE_KEY: (True, None)}
        assert _bodies_of_versions(env, FINAL_KEY) == [b"B" * 16]

    def test_a_large_upload_is_queued_with_its_own_staging_key(self, env):
        upload_id, entry = _start(b"A" * 16)
        key = _staging_key_of(env, entry["uploadIdS3"])
        queue = MagicMock(return_value=True)
        with patch.object(uf, "should_process_asynchronously", return_value=True), \
                patch.object(uf, "queue_large_file_for_processing", queue):
            response = _complete(upload_id, entry)

        assert _results(response) == {RELATIVE_KEY: (True, None)}
        queue.assert_called_once()
        assert queue.call_args.args[0]["tempS3Key"] == key != LEGACY_STAGING_KEY
