# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""No file is written into a location another live asset also holds (#1207 D6).

Bucket sync cannot tell which of two live assets on one stored location a new file is for: it sends the
record to the rejected-record queue and writes no ledger row or search document. So an upload (initialize,
the multipart completion with its zero-byte branch, and the external completion that workflow outputs use),
a new folder, a copy into the asset, a revert, an unarchive and a primary-file change are refused before
anything is written, with SHARED_LOCATION_WRITE_REFUSAL, which names the condition (never the other asset)
and the troubleshooting section that resolves it. The check is the shared-location rule of the destructive
operations (D2), read from the location index once it is ready; an archived asset on the location does not
count, and before the index is ready writes behave as before.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import assetFiles  # noqa: E402
from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402
from backend.backend.models.assetsV3 import CreateFolderRequestModel  # noqa: E402
from tests.common.fileAttributionFakes import (  # noqa: E402
    AttributionAssetTable, AttributionBucketsTable, asset_row, registration,
)
from tests.handlers.assets import test_uploadFile_completion_blocklist as completion  # noqa: E402
from tests.handlers.assets.test_large_file_workflow_provenance import _external_request  # noqa: E402
from tests.handlers.assets.test_uploadFile_preview_extension_init import (  # noqa: E402
    _initialize_upload_env, _request as _init_request,
)
from tests.handlers.workflows.test_output_metadata_type_key import po  # noqa: E402

al = sys.modules['common.assetLocations']
# Read with a fallback so the behaviour tests also run (and fail) on code without the rule
REFUSAL = getattr(al, "SHARED_LOCATION_WRITE_REFUSAL", "(no shared-location write refusal)")
SECTION = "Bucket Sync Rejected-Record Queue Holds Messages"

# The upload harnesses' asset: db-1/asset-1 at asset-1/ in asset-bucket
UP_BUCKET = "asset-bucket"
UP_SELF = asset_row("db-1", "asset-1", "bucket-1", "asset-1/", UP_BUCKET)
UP_OTHER = asset_row("db-2", "other", "bucket-1", "asset-1/", UP_BUCKET)


@pytest.fixture(autouse=True)
def _ready(monkeypatch):
    al.clear_nested_locations_cache()
    monkeypatch.setattr(al, "index_ready", lambda name: True)
    yield
    al.clear_nested_locations_cache()


@pytest.mark.unit
class TestTheMessage:
    def test_it_names_the_condition_and_the_section_and_no_asset(self):
        assert "another asset" in REFUSAL and SECTION in REFUSAL
        assert "db-2" not in REFUSAL and "other" not in REFUSAL.replace("another", "")

    def test_the_troubleshooting_section_names_the_refusal(self):
        guide = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "documentation", "docusaurus-site",
                             "docs", "troubleshooting", "common-issues.md")
        text = open(guide, encoding="utf-8").read()
        section = text[text.index(f"### {SECTION}"):]
        section = section[:section.index("\n### ", 4)]
        assert REFUSAL.split(" See ")[0] in section


@pytest.mark.unit
class TestTheUploadRule:
    def _refusal(self, monkeypatch, rows, key="asset-1/new.txt"):
        monkeypatch.setattr(uploadFile, "asset_table", AttributionAssetTable(rows))
        monkeypatch.setattr(uploadFile, "buckets_table", AttributionBucketsTable([]))
        return uploadFile.upload_path_refusal(dict(UP_SELF), UP_BUCKET, "asset-1/", key)

    def test_a_key_of_a_shared_location_is_refused(self, monkeypatch):
        assert self._refusal(monkeypatch, [UP_SELF, UP_OTHER]) == REFUSAL

    def test_an_archived_co_owner_and_the_asset_alone_are_not(self, monkeypatch):
        assert self._refusal(monkeypatch, [UP_SELF]) is None
        archived = asset_row("db-2", "other", "bucket-1", "asset-1/", UP_BUCKET, archived=True)
        assert self._refusal(monkeypatch, [UP_SELF, archived]) is None

    def test_a_nested_asset_inside_a_shared_location_keeps_its_own_answer(self, monkeypatch):
        inner = asset_row("db-3", "inner", "bucket-1", "asset-1/inner/", UP_BUCKET)
        rows = [UP_SELF, UP_OTHER, inner]
        assert self._refusal(monkeypatch, rows, "asset-1/inner/x.txt") == al.NESTED_PATH_REFUSAL

    def test_before_the_index_is_ready_writes_behave_as_before(self, monkeypatch):
        monkeypatch.setattr(al, "index_ready", lambda name: False)
        assert self._refusal(monkeypatch, [UP_SELF, UP_OTHER]) is None


def _shared_tables():
    return (patch.object(uploadFile, "asset_table", AttributionAssetTable([UP_SELF, UP_OTHER])),
            patch.object(uploadFile, "buckets_table", AttributionBucketsTable([])))


@pytest.mark.unit
class TestUploadsAreRefusedBeforeAnythingIsWritten:
    def test_initialize_refuses_and_starts_no_multipart_upload(self):
        with _initialize_upload_env(completion._asset()) as (uf, mock_s3):
            tables = _shared_tables()
            with tables[0], tables[1], pytest.raises(uf.VAMSGeneralErrorResponse) as raised:
                uf.initialize_upload(_init_request("assetFile", "/scan.e57"), {"tokens": ["alice@corp"]})
        assert str(raised.value).endswith(": " + REFUSAL)
        mock_s3.create_multipart_upload.assert_not_called()

    def test_control_initialize_starts_the_upload_on_an_unshared_location(self):
        with _initialize_upload_env(completion._asset()) as (uf, mock_s3), \
                patch.object(uf, "asset_table", AttributionAssetTable([UP_SELF])), \
                patch.object(uf, "buckets_table", AttributionBucketsTable([])):
            uf.initialize_upload(_init_request("assetFile", "/scan.e57"), {"tokens": ["alice@corp"]})
        mock_s3.create_multipart_upload.assert_called_once()

    @pytest.mark.parametrize("file", [completion._multipart("/scan.e57"), completion._zero_byte("/empty.txt")],
                             ids=["multipart", "zero-byte"])
    def test_the_completion_refuses_each_file_and_writes_nothing(self, file):
        record = completion._upload_record([file["relativeKey"]])
        with completion._completion_env(record) as (uf, mock_s3, mock_copy):
            tables = _shared_tables()
            with tables[0], tables[1]:
                response = uf.complete_upload(completion.UPLOAD_ID, completion._request(file),
                                              completion._rest_event())
        result = completion._result(response, file["relativeKey"])
        assert (result.success, result.error) == (False, REFUSAL)
        mock_copy.assert_not_called()
        mock_s3.complete_multipart_upload.assert_not_called()
        mock_s3.put_object.assert_not_called()

    def test_the_external_completion_of_a_workflow_output_refuses_the_file(self):
        uf = uploadFile
        request_model = _external_request(files=[{"relativeKey": "/out/a.txt", "tempKey": "temp/up-1/x"}])
        tables = _shared_tables()
        with patch.object(uf, "get_upload_details", return_value={
            "assetId": "asset-1", "databaseId": "db-1", "uploadType": "assetFile",
            "isExternalUpload": True, "temporaryPrefix": "temp/up-1/"}), \
                patch.object(uf, "get_asset_details", return_value=completion._asset()), \
                patch.object(uf, "get_default_bucket_details", return_value={
                    "bucketId": "bucket-1", "bucketName": UP_BUCKET, "baseAssetsPrefix": ""}), \
                patch.object(uf, "get_database_details", return_value={"databaseId": "db-1"}), \
                patch.object(uf, "asset_upload_table", MagicMock()), \
                patch.object(uf, "delete_upload_details", MagicMock()), \
                patch.object(uf, "copy_s3_object", return_value=True) as mock_copy, \
                patch.object(uf, "queue_large_file_for_processing", return_value=True) as queued, \
                patch.object(uf, "s3", MagicMock()) as mock_s3, tables[0], tables[1]:
            mock_s3.head_object.return_value = {"ContentLength": 1024}
            uf.claims_and_roles = {"tokens": ["alice@corp"]}
            response = uf.complete_external_upload("up-1", request_model, {})
        [result] = response.fileResults
        assert (result.success, result.error) == (False, REFUSAL)
        mock_copy.assert_not_called()
        queued.assert_not_called()

    def test_a_workflow_run_names_the_refusal_in_its_error(self):
        reasons = po._refused_write_back_reasons({"fileResults": [
            {"relativeKey": "/out/a.txt", "success": False, "error": REFUSAL}]})
        # Keyed as the output step keys its outputs, without the leading '/' the completion is sent
        assert reasons == {"out/a.txt": REFUSAL}


# assetFiles operations on two live assets at p/shared/ (db1/a and db2/b); c at p/other/ is the copy source
BUCKET = "vams-shared"
A = asset_row("db1", "a", "reg-1", "p/shared/", BUCKET)
B = asset_row("db2", "b", "reg-1", "p/shared/", BUCKET)
C = asset_row("db1", "c", "reg-1", "p/other/", BUCKET)
CLAIMS = {"tokens": ["tester"], "roles": []}


@pytest.fixture
def shared_bucket(monkeypatch):
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for key in ("p/shared/x.txt", "p/other/y.txt"):
            client.put_object(Bucket=BUCKET, Key=key, Body=b"v1")
            client.put_object(Bucket=BUCKET, Key=key, Body=b"v2")
        af = assetFiles
        rows = {("db1", "a"): A, ("db2", "b"): B, ("db1", "c"): C}
        monkeypatch.setattr(af, "asset_table", AttributionAssetTable([A, B, C]))
        monkeypatch.setattr(af, "buckets_table", AttributionBucketsTable([registration("reg-1", BUCKET, "/")]))
        monkeypatch.setattr(af, "s3_client", client)
        resource = MagicMock(name="s3_resource")
        monkeypatch.setattr(af, "s3_resource", resource)
        monkeypatch.setattr(af, "get_asset_with_permissions",
                            lambda database_id, asset_id, *args, **kwargs: dict(rows[(database_id, asset_id)]))
        monkeypatch.setattr(af, "get_default_bucket_details",
                            lambda bucket_id: {"bucketId": bucket_id, "bucketName": BUCKET, "baseAssetsPrefix": "/"})
        yield client, resource


def _versions(client):
    listed = client.list_object_versions(Bucket=BUCKET)
    return (sorted((v["Key"], v["VersionId"]) for v in listed.get("Versions", [])),
            listed.get("DeleteMarkers", []))


@pytest.mark.unit
class TestFileOperationsThatWriteAreRefused:
    def _first_version(self, client, key):
        return [v for v in client.list_object_versions(Bucket=BUCKET, Prefix=key)["Versions"]
                if v["Key"] == key and not v["IsLatest"]][0]["VersionId"]

    @pytest.mark.parametrize("operation", ["createFolder", "copyInto", "revert", "setPrimary", "unarchive"])
    def test_the_write_is_refused_and_changes_nothing(self, shared_bucket, operation):
        client, resource = shared_bucket
        af = assetFiles
        if operation == "unarchive":
            client.delete_object(Bucket=BUCKET, Key="p/shared/x.txt")
        before = _versions(client)
        calls = {
            "createFolder": lambda: af.create_folder("db1", "a", CreateFolderRequestModel(relativeKey="new/"),
                                                     CLAIMS),
            "copyInto": lambda: af.copy_file("db1", "c", "/y.txt", "/y.txt", "a", "db1", CLAIMS),
            "revert": lambda: af.revert_file_version("db1", "a", "/x.txt",
                                                     self._first_version(client, "p/shared/x.txt"), CLAIMS),
            "setPrimary": lambda: af.set_primary_file("db1", "a", "/x.txt", "primary", None, CLAIMS),
            "unarchive": lambda: af.unarchive_file("db1", "a", "/x.txt", CLAIMS),
        }
        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            calls[operation]()
        assert str(raised.value).endswith(": " + REFUSAL)
        assert _versions(client) == before
        resource.Object.assert_not_called()
        resource.meta.client.copy.assert_not_called()

    def test_control_a_copy_into_an_unshared_asset_is_not_refused_by_the_rule(self, shared_bucket):
        client, _ = shared_bucket
        af = assetFiles
        nested = af.nested_locations_for(dict(C), BUCKET, "p/other/", include_shared=True)
        af.refuse_unwritable_key(dict(C), BUCKET, "p/other/", "p/other/z.txt", al.NESTED_PATH_REFUSAL)
        assert not nested
