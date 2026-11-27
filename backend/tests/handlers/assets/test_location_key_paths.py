# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A file path that starts with the asset's location key names the file at that path in the asset (#1943).

The file routes accept a file's path in the asset (``/sub/x``, the file list's ``relativePath``) and its full
S3 key (``A1/sub/x``, the file list's ``key``). They told them apart by one guess: after a leading ``/`` was
dropped, a value that started with the location key was used as the full key. So in an asset ``A1`` stored at
``A1/`` with a subfolder ``A1/``, the path ``/A1/cloud.las`` (key ``A1/A1/cloud.las``) was read as the root
file ``A1/cloud.las``: an upload to it replaced the root file's current version, and delete, archive and
rename acted on the root file. A leading ``/`` now always marks a path in the asset
(``common.assetLocations.object_key_for_request``); a value without one is the full key when the location
holds it. Each case runs the real handler against a versioned moto bucket (file routes) or stubbed clients
(upload, stream, workflow input) and checks the key S3 is asked for, and that the other file keeps its
versions.
"""

import os
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402
from backend.backend.handlers.assets import assetVersions as av  # noqa: E402
from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402
from backend.tests.handlers.assets.test_uploadFile_preview_extension_init import (  # noqa: E402
    _asset as _upload_asset, _initialize_upload_env, _request)
from backend.tests.handlers.assets.test_large_file_workflow_provenance import _external_request  # noqa: E402
from backend.tests.handlers.assets.test_streamAsset_range_header_casing import _load, _rest_event, _wire  # noqa: E402
from backend.tests.handlers.workflows.test_executeWorkflow import ewv2  # noqa: E402
from backend.tests.handlers.workflows.test_output_metadata_type_key import po  # noqa: E402

BUCKET = "vams-test-assets"
CLAIMS = {"tokens": ["tester"], "roles": []}

# An asset stored at its id, holding a root file and a file in a subfolder named like the asset
LOCATION = "A1/"
ROOT_FILE = "A1/cloud.las"
SUB_FILE = "A1/A1/cloud.las"
# An asset under a bucket base prefix, whose subfolder repeats the whole location
PREFIXED_LOCATION = "vams/B1/"
PREFIXED_ROOT = "vams/B1/x.las"
PREFIXED_SUB = "vams/B1/vams/B1/x.las"
# A key with an empty segment right after the location, which uploads before 2.7.0 could write; its path in
# the asset (relative_path_in) is '//cloud.las'
EMPTY_SEGMENT_FILE = "A1//cloud.las"
KEYS = (ROOT_FILE, SUB_FILE, "A1/A1/other.las", PREFIXED_ROOT, PREFIXED_SUB, EMPTY_SEGMENT_FILE)


def _live(client, key):
    try:
        client.head_object(Bucket=BUCKET, Key=key)
        return True
    except client.exceptions.ClientError:
        return False


def _versions(client, key):
    listing = client.list_object_versions(Bucket=BUCKET, Prefix=key)
    return ([v for v in listing.get("Versions", []) if v["Key"] == key]
            + [m for m in listing.get("DeleteMarkers", []) if m["Key"] == key])


@pytest.fixture
def af(monkeypatch):
    """assetFiles against a versioned moto bucket; the asset record names the location under test."""
    from backend.backend.handlers.assets import assetFiles as module

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for key in KEYS:
            client.put_object(Bucket=BUCKET, Key=key, Body=b"content")

        asset = {"databaseId": "db1", "assetId": "A1", "bucketId": "b1", "assetLocation": {"Key": LOCATION}}
        from tests.common.fileAttributionFakes import AttributionAssetTable, AttributionBucketsTable, registration
        monkeypatch.setattr(module, "asset_table", AttributionAssetTable([asset]))
        monkeypatch.setattr(module, "buckets_table", AttributionBucketsTable([registration("b1", BUCKET, "")]))
        monkeypatch.setattr(module, "s3_client", client)
        monkeypatch.setattr(module, "s3_resource", boto3.resource("s3", region_name="us-east-1"))
        monkeypatch.setattr(module, "get_asset_with_permissions", lambda *args, **kwargs: asset)
        monkeypatch.setattr(module, "get_default_bucket_details",
                            lambda bucket_id: {"bucketId": bucket_id, "bucketName": BUCKET,
                                               "baseAssetsPrefix": ""})
        monkeypatch.setattr(module, "asset_file_version_history_table", None)
        monkeypatch.setattr(module, "MAX_PARALLEL_S3_WORKERS", 1)
        for name in ("send_subscription_email", "delete_assetAuxiliary_files", "delete_file_version_records",
                     "move_auxiliary_files", "copy_auxiliary_files"):
            monkeypatch.setattr(module, name, lambda *args, **kwargs: None)
        module.metadata_deleted = []
        module.metadata_copied = []
        monkeypatch.setattr(module, "delete_file_metadata",
                            lambda db, asset_id, path: module.metadata_deleted.append(path))
        monkeypatch.setattr(module, "_copy_file_metadata_to_destination",
                            lambda sdb, sa, spath, ddb, da, dpath: module.metadata_copied.append((spath, dpath)) or 0)
        module.test_client = client
        module.test_asset = asset
        yield module


def _prefixed(af):
    af.test_asset["assetLocation"] = {"Key": PREFIXED_LOCATION}


@pytest.mark.unit
class TestResolveTheFileRoutesPath:
    @pytest.mark.parametrize("location,path,expected", [
        (LOCATION, "/A1/cloud.las", SUB_FILE),
        (LOCATION, SUB_FILE, SUB_FILE),
        (LOCATION, "/cloud.las", ROOT_FILE),
        (LOCATION, ROOT_FILE, ROOT_FILE),
        (LOCATION, "cloud.las", ROOT_FILE),
        (PREFIXED_LOCATION, "/vams/B1/x.las", PREFIXED_SUB),
        (PREFIXED_LOCATION, PREFIXED_SUB, PREFIXED_SUB),
        (PREFIXED_LOCATION, PREFIXED_ROOT, PREFIXED_ROOT),
        (PREFIXED_LOCATION, "/x.las", PREFIXED_ROOT),
    ])
    def test_a_leading_slash_marks_a_path_in_the_asset(self, af, location, path, expected):
        assert af.resolve_asset_file_path(location, path) == expected

    @pytest.mark.parametrize("path", ["/../A2/x.las", "../x.las", "sub/../../x.las"])
    def test_a_parent_segment_is_refused_with_a_fixed_message(self, af, path):
        with pytest.raises(af.VAMSGeneralErrorResponse) as e:
            af.resolve_asset_file_path(LOCATION, path)
        assert str(e.value).endswith("Requested file is not part of this asset")
        assert "A2" not in str(e.value)

    def test_another_assets_key_stays_inside_this_asset(self, af):
        assert af.resolve_asset_file_path(LOCATION, "A2/cloud.las") == "A1/A2/cloud.las"


@pytest.mark.unit
class TestFileOperationsOnTheSubfolderFile:
    """A5: every file operation on '/A1/...' acts on 'A1/A1/...' only; the root file keeps its versions."""

    def test_file_info_names_the_subfolder_file(self, af):
        assert af.get_file_info("db1", "A1", "/A1/cloud.las", False, CLAIMS).relativePath == "/A1/cloud.las"
        assert af.get_file_info("db1", "A1", SUB_FILE, False, CLAIMS).relativePath == "/A1/cloud.las"
        assert af.get_file_info("db1", "A1", "/cloud.las", False, CLAIMS).relativePath == "/cloud.las"
        assert af.get_file_info("db1", "A1", ROOT_FILE, False, CLAIMS).relativePath == "/cloud.las"

    def test_archive_and_unarchive_act_on_the_subfolder_file(self, af):
        af.archive_file("db1", "A1", "/A1/cloud.las", False, CLAIMS)
        assert not _live(af.test_client, SUB_FILE)
        assert _live(af.test_client, ROOT_FILE) and len(_versions(af.test_client, ROOT_FILE)) == 1
        af.unarchive_file("db1", "A1", "/A1/cloud.las", CLAIMS)
        assert _live(af.test_client, SUB_FILE)
        assert len(_versions(af.test_client, ROOT_FILE)) == 1

    def test_permanent_delete_removes_the_subfolder_file_only(self, af):
        af.delete_file("db1", "A1", "/A1/cloud.las", False, True, CLAIMS)
        assert _versions(af.test_client, SUB_FILE) == []
        assert _live(af.test_client, ROOT_FILE) and len(_versions(af.test_client, ROOT_FILE)) == 1
        assert af.metadata_deleted == ["/A1/cloud.las"]

    def test_a_prefix_archive_of_the_subfolder_leaves_the_root_file(self, af):
        response = af.archive_file("db1", "A1", "/A1/", True, CLAIMS)
        assert sorted(path.lstrip("/") for path in response.affectedFiles) == ["A1/cloud.las", "A1/other.las"]
        assert _live(af.test_client, ROOT_FILE)

    def test_rename_in_the_subfolder_moves_the_subfolder_file(self, af):
        af.move_file("db1", "A1", "/A1/cloud.las", "/A1/renamed.las", CLAIMS)
        assert _live(af.test_client, "A1/A1/renamed.las") and not _live(af.test_client, SUB_FILE)
        assert _live(af.test_client, ROOT_FILE) and len(_versions(af.test_client, ROOT_FILE)) == 1
        assert not _live(af.test_client, "A1/renamed.las")

    def test_copy_from_the_subfolder_reads_the_subfolder_file(self, af):
        af.test_client.put_object(Bucket=BUCKET, Key=SUB_FILE, Body=b"subfolder")
        af.copy_file("db1", "A1", "/A1/cloud.las", "/copied.las", None, None, CLAIMS)
        body = af.test_client.get_object(Bucket=BUCKET, Key="A1/copied.las")["Body"].read()
        assert body == b"subfolder"
        assert len(_versions(af.test_client, ROOT_FILE)) == 1

    def test_create_folder_in_the_subfolder(self, af):
        from models.assetsV3 import CreateFolderRequestModel
        af.create_folder("db1", "A1", CreateFolderRequestModel(relativeKey="/A1/new/"), CLAIMS)
        assert _live(af.test_client, "A1/A1/new/") and not _live(af.test_client, "A1/new/")

    @pytest.mark.parametrize("source,dest", [(SUB_FILE, "A1/A1/moved.las"), ("/A1/cloud.las", "/A1/moved.las")])
    def test_a_move_keys_the_metadata_by_the_moved_files_listed_paths(self, af, source, dest):
        """The metadata copy names the files by their listed paths, whichever form the request used."""
        af.move_file("db1", "A1", source, dest, CLAIMS)
        assert af.metadata_copied == [("/A1/cloud.las", "/A1/moved.las")]

    @pytest.mark.parametrize("path", [SUB_FILE, "/A1/cloud.las"])
    def test_a_delete_keys_the_metadata_by_the_files_listed_path(self, af, path):
        af.delete_file("db1", "A1", path, False, True, CLAIMS)
        assert af.metadata_deleted == ["/A1/cloud.las"]


@pytest.mark.unit
class TestABasePrefixedAsset:
    def test_archive_of_the_repeated_prefix_path_acts_on_the_subfolder_file(self, af):
        _prefixed(af)
        af.archive_file("db1", "A1", "/vams/B1/x.las", False, CLAIMS)
        assert not _live(af.test_client, PREFIXED_SUB)
        assert _live(af.test_client, PREFIXED_ROOT)

    def test_file_info_by_the_full_key_names_the_root_file(self, af):
        _prefixed(af)
        assert af.get_file_info("db1", "A1", PREFIXED_ROOT, False, CLAIMS).relativePath == "/x.las"


@pytest.mark.unit
class TestUploads:
    """A1: an upload to '/A1/cloud.las' writes 'A1/A1/cloud.las', never a new version of 'A1/cloud.las'."""

    @pytest.mark.parametrize("relative,expected", [
        ("/A1/cloud.las", SUB_FILE),
        (SUB_FILE, SUB_FILE),
        ("/cloud.las", ROOT_FILE),
        (ROOT_FILE, ROOT_FILE),
        ("/A1/cloud.las.previewFile.png", "A1/A1/cloud.las.previewFile.png"),
    ])
    def test_initialize_stages_the_key_the_path_names(self, relative, expected):
        with _initialize_upload_env(_upload_asset(assetLocation={"Key": LOCATION})) as (uf, mock_s3):
            uf.initialize_upload(_request("assetFile", relative), CLAIMS)
        key = mock_s3.create_multipart_upload.call_args.kwargs["Key"]
        # The staging key ends with the final key; the root file's staging key never ends with the subfolder's
        assert key.endswith(expected)
        if expected == ROOT_FILE:
            assert not key.endswith(SUB_FILE)

    @pytest.mark.parametrize("relative,expected", [("/A1/out.las", "A1/A1/out.las"), ("/out.las", "A1/out.las"),
                                                   ("A1/out.las", "A1/out.las")])
    def test_an_external_completion_writes_the_key_the_path_names(self, relative, expected):
        from backend.backend.handlers.assets import uploadFile as uf
        request_model = _external_request(files=[{"relativeKey": relative, "tempKey": "temp/up-1/x"}])
        with patch.object(uf, 'get_upload_details', return_value={
            'assetId': 'asset-1', 'databaseId': 'db-1', 'uploadType': 'assetFile',
            'isExternalUpload': True, 'temporaryPrefix': 'temp/up-1/',
        }), patch.object(uf, 'get_asset_details', return_value={
            'assetId': 'asset-1', 'databaseId': 'db-1', 'bucketId': 'bucket-1', 'assetLocation': {'Key': LOCATION},
        }), patch.object(uf, 'get_default_bucket_details', return_value={
            'bucketId': 'bucket-1', 'bucketName': 'asset-bucket', 'baseAssetsPrefix': '',
        }), patch.object(uf, 'get_database_details', return_value={'databaseId': 'db-1'}), \
                patch.object(uf, 'asset_upload_table', MagicMock()), \
                patch.object(uf, 'delete_upload_details', MagicMock()), \
                patch.object(uf, 's3', MagicMock()) as mock_s3, \
                patch.object(uf, 'queue_large_file_for_processing', return_value=True) as queued:
            mock_s3.head_object.return_value = {'ContentLength': 6 * 1024 ** 3}
            uf.claims_and_roles = {"tokens": ["alice@corp"]}
            uf.complete_external_upload("up-1", request_model, {})
        assert queued.call_args.args[0]["finalS3Key"] == expected

    def test_a_parent_segment_is_refused(self):
        from backend.backend.handlers.assets import uploadFile as uf
        with pytest.raises(uf.VAMSGeneralErrorResponse):
            uf.normalize_s3_path(LOCATION, "/A1/../../A2/x.las")


@pytest.fixture
def stream():
    m = _load()
    s3 = _wire(m)
    s3.head_object.return_value = {"ContentType": "application/octet-stream", "ContentLength": 1024,
                                   "ResponseMetadata": {"HTTPHeaders": {}}}
    m.get_asset_details = MagicMock(return_value={
        "databaseId": "db1", "assetId": "A1", "isDistributable": True,
        "bucketId": "bucket-1", "assetLocation": {"Key": LOCATION},
    })
    return m, s3


def _stream(m, proxy, method="GET"):
    event = _rest_event({"Range": "bytes=0-9"}, method=method)
    event["pathParameters"]["proxy"] = proxy
    return m.lambda_handler(event, None)


@pytest.mark.unit
class TestStream:
    """A2: the stream route (GET and HEAD) reads the key the path names; '/A1/cloud.las' arrives as the proxy
    '/A1/cloud.las' (a 'stream//A1/cloud.las' URL, as search results send it)."""

    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    @pytest.mark.parametrize("proxy,expected", [
        ("/A1/cloud.las", SUB_FILE),
        (SUB_FILE, SUB_FILE),
        ("/cloud.las", ROOT_FILE),
        (ROOT_FILE, ROOT_FILE),
        ("cloud.las", ROOT_FILE),
    ])
    def test_the_stream_reads_the_key_the_path_names(self, stream, method, proxy, expected):
        m, s3 = stream
        _stream(m, proxy, method=method)
        reader = s3.get_object if method == "GET" else s3.head_object
        assert reader.call_args.kwargs["Key"] == expected

    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_a_version_pin_of_the_subfolder_file_uses_its_own_path(self, stream, method):
        m, s3 = stream
        m.resolve_file_version_from_asset_version = MagicMock(return_value="v-pinned")
        event = _rest_event({"Range": "bytes=0-9"}, method=method)
        event["pathParameters"]["proxy"] = "/A1/cloud.las"
        event["queryStringParameters"] = {"assetVersionId": "2"}
        m.lambda_handler(event, None)
        m.resolve_file_version_from_asset_version.assert_called_once_with("db1", "asset1", "2", "/A1/cloud.las")

    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    @pytest.mark.parametrize("asset_exists", [True, False])
    def test_a_refused_caller_gets_403_before_any_s3_call(self, stream, method, asset_exists):
        m, s3 = stream
        if not asset_exists:
            m.get_asset_details.return_value = None
        m.CasbinEnforcer.return_value.enforce.return_value = False
        response = _stream(m, "/A1/cloud.las", method=method)
        assert response["statusCode"] == 403
        s3.get_object.assert_not_called()
        s3.head_object.assert_not_called()

    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_a_parent_segment_is_refused_before_any_s3_call(self, stream, method):
        m, s3 = stream
        response = _stream(m, "/A1/../A2/x.las", method=method)
        assert response["statusCode"] == 400
        s3.get_object.assert_not_called()
        s3.head_object.assert_not_called()


SINGLE_FILE = "p/inner.txt"


@pytest.mark.unit
class TestStoredVersionPathsOfASingleFileAsset:
    """R2: a version record's relativeKey is a path in the asset, never a key. In a single-file asset ``p/inner.txt``
    the companion entry whose path spells the location (``/p/inner.txt/x.obj``) is the key
    ``p/inner.txt/p/inner.txt/x.obj``, not the companion ``p/inner.txt/x.obj``."""

    @pytest.mark.parametrize("relative,expected", [
        ("p/inner.txt/x.obj", "p/inner.txt/p/inner.txt/x.obj"),
        ("x.obj", "p/inner.txt/x.obj"),
        ("inner.txt", SINGLE_FILE),
        ("inner.txt.previewFile.png", "p/inner.txt.previewFile.png"),
    ])
    def test_version_create_checks_the_key_the_path_names(self, relative, expected):
        from models.assetsV3 import AssetFileVersionItemModel
        client = MagicMock()
        with patch.object(av, "s3_client", client), patch.object(av, "is_file_archived", return_value=False):
            assert av.validate_s3_files_exist("bkt", SINGLE_FILE, [
                AssetFileVersionItemModel(relativeKey=relative, versionId="v1")]) == []
        assert client.head_object.call_args.kwargs["Key"] == expected

    def test_a_revert_restores_the_companion_entry_the_path_names(self):
        request = MagicMock(assetVersionId="1", revertMetadata=False, comment="")
        copy = MagicMock(return_value="s3v-new")
        files = [{"relativeKey": "p/inner.txt/x.obj", "versionId": "s3v1", "size": 1, "etag": "e"}]
        with patch.object(av, "get_asset_with_permissions", return_value={"databaseId": "db1", "assetId": "a1"}), \
                patch.object(av, "get_asset_s3_location", return_value=("bkt", SINGLE_FILE)), \
                patch.object(av, "nested_locations_for", return_value=MagicMock(keep=lambda key: True)), \
                patch.object(av, "get_asset_version_metadata", return_value={"assetVersionId": "1"}), \
                patch.object(av, "get_asset_file_versions", return_value={"files": files}), \
                patch.object(av, "reserve_next_asset_version", return_value="3"), \
                patch.object(av, "does_file_version_exist", return_value=True), \
                patch.object(av, "copy_s3_object_version", copy), \
                patch.object(av, "delete_assetAuxiliary_files", MagicMock()), \
                patch.object(av, "save_asset_file_versions", return_value=True), \
                patch.object(av, "save_asset_metadata_version", return_value=True), \
                patch.object(av, "stamp_asset_version_provenance", MagicMock()), \
                patch.object(av, "finalize_asset_version", MagicMock()), \
                patch.object(av, "send_subscription_email", MagicMock()), \
                patch.object(av, "MAX_PARALLEL_S3_WORKERS", 1):
            av.revert_asset_version("db1", "a1", request, {"tokens": ["tester"], "roles": []})
        assert copy.call_args.args[1] == "p/inner.txt/p/inner.txt/x.obj"


@pytest.mark.unit
class TestWorkflowInputs:
    """A3: a workflow input 'db:A1:/A1/cloud.las' runs on 'A1/A1/cloud.las'."""

    @pytest.mark.parametrize("relative,expected", [
        ("/A1/cloud.las", SUB_FILE),
        ("/cloud.las", ROOT_FILE),
        ("/A1/", "A1/A1/"),
        ("/", "A1/"),
        ("", "A1/"),
    ])
    def test_the_input_key_is_the_path_in_the_asset(self, relative, expected):
        assert ewv2._input_key(LOCATION, relative) == expected

    @pytest.mark.parametrize("relative,expected", [("/vams/B1/x.las", PREFIXED_SUB), ("/x.las", PREFIXED_ROOT)])
    def test_a_base_prefixed_asset(self, relative, expected):
        assert ewv2._input_key(PREFIXED_LOCATION, relative) == expected


@pytest.mark.unit
class TestWorkflowOutputWriteBack:
    """A3: an output a pipeline writes into the subfolder named like the location lands in that subfolder. The
    output step sends each asset file's path with a leading '/', and the upload handler reads it as a path."""

    @pytest.mark.parametrize("relative,sent", [("A1/out.las", "/A1/out.las"), ("out.las", "/out.las"),
                                               ("/sub/out.las", "/sub/out.las")])
    def test_an_asset_file_is_sent_as_its_path_in_the_asset(self, relative, sent):
        assert po._completion_request_key(relative, "assetFile") == sent

    def test_a_preview_keeps_its_basename(self):
        assert po._completion_request_key("thumb.png", "assetPreview") == "thumb.png"

    def test_the_completion_lists_each_file_by_its_path(self):
        ingest = MagicMock(return_value={"Payload": MagicMock(read=MagicMock(return_value=b'{"statusCode": 200, '
                                                                             b'"body": "{}"}'))})
        with patch.object(po, "_lambda_file_ingestion", ingest):
            po.process_external_upload("up-1", "A1", "db1", "assetFile", ["run/out/A1/out.las"], "run/out/",
                                       {"http": {}})
        import json
        files = json.loads(ingest.call_args.args[0]["body"])["files"]
        assert files == [{"relativeKey": "/A1/out.las", "tempKey": "run/out/A1/out.las"}]

    @pytest.mark.parametrize("location,relative,expected", [
        (LOCATION, "A1/out.las", "A1/A1/out.las"),
        (LOCATION, "out.las", "A1/out.las"),
        (PREFIXED_LOCATION, "vams/B1/out.las", "vams/B1/vams/B1/out.las"),
        ("p1/model.glb", "model.glb", "p1/model.glb"),
        ("p1/model.glb", "p1/model.glb/x.obj", "p1/model.glb/p1/model.glb/x.obj"),
    ])
    def test_the_destination_check_resolves_as_the_upload_does(self, location, relative, expected):
        asset = {"assetId": "A1", "assetLocation": {"Key": location}}
        assert po._destination_key({"relativeFilePath": relative}, asset, "") == expected
        assert uploadFile.normalize_s3_path(location, po._completion_request_key(relative, "assetFile")) == expected

    def test_the_results_are_read_by_the_outputs_paths(self):
        result = {"overallSuccess": False, "fileResults": [
            {"relativeKey": "/A1/out.las", "success": True},
            {"relativeKey": "/big.e57", "success": True, "ingestionJobId": "job-1"},
            {"relativeKey": "/bad.las", "success": False, "error": po.NESTED_PATH_REFUSAL}]}
        assert po._ingestion_outcome(result) == (False, {"A1/out.las", "big.e57"})
        assert po._pending_ingestion_jobs(result) == {"big.e57": "job-1"}
        assert po._refused_write_back_reasons(result) == {"bad.las": po.NESTED_PATH_REFUSAL}


@pytest.mark.unit
class TestAnEmptySegmentKeyIsReachableByItsPath:
    """L4 (#1943): exactly one leading '/' marks a path in the asset, so '//cloud.las', the path relative_path_in
    gives the key 'A1//cloud.las' (an empty segment right after the location), names that key on every route,
    and the root file 'A1/cloud.las' keeps its versions. One leading slash names what it named before. (The
    file list's own paths are not tested here; it still shows such a key at '/cloud.las', #1977.)"""

    def test_file_info_names_the_empty_segment_key(self, af):
        assert af.resolve_asset_file_path(LOCATION, "//cloud.las") == EMPTY_SEGMENT_FILE
        assert af.get_file_info("db1", "A1", "//cloud.las", False, CLAIMS).relativePath == "//cloud.las"
        assert af.get_file_info("db1", "A1", "/cloud.las", False, CLAIMS).relativePath == "/cloud.las"

    def test_delete_removes_the_empty_segment_key_only(self, af):
        af.delete_file("db1", "A1", "//cloud.las", False, True, CLAIMS)
        assert _versions(af.test_client, EMPTY_SEGMENT_FILE) == []
        assert _live(af.test_client, ROOT_FILE) and len(_versions(af.test_client, ROOT_FILE)) == 1
        assert af.metadata_deleted == ["//cloud.las"]

    def test_move_moves_the_empty_segment_key_only(self, af):
        af.move_file("db1", "A1", "//cloud.las", "/moved.las", CLAIMS)
        assert _live(af.test_client, "A1/moved.las") and not _live(af.test_client, EMPTY_SEGMENT_FILE)
        assert _live(af.test_client, ROOT_FILE) and len(_versions(af.test_client, ROOT_FILE)) == 1
        assert af.metadata_copied == [("//cloud.las", "/moved.las")]

    @pytest.mark.parametrize("value,expected", [("//cloud.las", EMPTY_SEGMENT_FILE), ("/cloud.las", ROOT_FILE),
                                                (EMPTY_SEGMENT_FILE, EMPTY_SEGMENT_FILE), (ROOT_FILE, ROOT_FILE)])
    def test_download_names_the_key_the_path_names(self, value, expected):
        import models.assetsV3 as m
        from common.downloadEligibility import resolve_asset_file_key
        assert m.DownloadAssetRequestModel(downloadType="assetFile", key=value).key == value
        assert m.DownloadAssetRequestModel(downloadType="assetFile", keys=[value]).keys
        assert resolve_asset_file_key(LOCATION, value) == expected

    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    @pytest.mark.parametrize("proxy,searched", [("//cloud.las", "//cloud.las"), ("/cloud.las", "/cloud.las"),
                                                (EMPTY_SEGMENT_FILE, "//cloud.las")])
    def test_a_version_pin_searches_the_snapshot_by_the_files_own_path(self, stream, method, proxy, searched):
        # #1977 Part C: the snapshot lookup drops one leading '/', so '//cloud.las' finds its own entry
        m, s3 = stream
        m.resolve_file_version_from_asset_version = MagicMock(return_value="v-pinned")
        event = _rest_event({"Range": "bytes=0-9"}, method=method)
        event["pathParameters"]["proxy"] = proxy
        event["queryStringParameters"] = {"assetVersionId": "2"}
        m.lambda_handler(event, None)
        m.resolve_file_version_from_asset_version.assert_called_once_with("db1", "asset1", "2", searched)

    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    @pytest.mark.parametrize("proxy,expected", [("//cloud.las", EMPTY_SEGMENT_FILE), ("/cloud.las", ROOT_FILE)])
    def test_the_stream_reads_the_key_the_path_names(self, stream, method, proxy, expected):
        m, s3 = stream
        _stream(m, proxy, method=method)
        reader = s3.get_object if method == "GET" else s3.head_object
        assert reader.call_args.kwargs["Key"] == expected

    def test_an_upload_stages_the_key_the_path_names(self):
        with _initialize_upload_env(_upload_asset(assetLocation={"Key": LOCATION})) as (uf, mock_s3):
            uf.initialize_upload(_request("assetFile", "//cloud.las"), CLAIMS)
        assert mock_s3.create_multipart_upload.call_args.kwargs["Key"].endswith("/" + EMPTY_SEGMENT_FILE)

    @pytest.mark.parametrize("model_name,extra", [
        ("GetFileMetadataRequestModel", {}),
        ("CreateFileMetadataRequestModel", {"metadata": [
            {"metadataKey": "k", "metadataValue": "v", "metadataValueType": "string"}]}),
        ("UpdateFileMetadataRequestModel", {"metadata": [
            {"metadataKey": "k", "metadataValue": "v", "metadataValueType": "string"}]}),
        ("DeleteFileMetadataRequestModel", {"metadataKeys": ["k"]}),
    ])
    def test_the_file_metadata_models_accept_the_listed_path(self, model_name, extra):
        import models.metadata as md
        assert getattr(md, model_name)(filePath="//cloud.las", type="metadata", **extra).filePath == "//cloud.las"

    def test_a_workflow_input_names_the_key_the_path_names(self):
        from models.executions import ExecuteInputFileModel
        item = ExecuteInputFileModel(databaseId="db1", assetId="A1", relativeFileKey="//cloud.las")
        assert ewv2._input_key(LOCATION, item.relativeFileKey) == EMPTY_SEGMENT_FILE
        assert ewv2._input_key(LOCATION, "/cloud.las") == ROOT_FILE

    def test_a_version_record_path_names_the_key(self):
        from models.assetsV3 import AssetFileVersionItemModel
        client = MagicMock()
        with patch.object(av, "s3_client", client), patch.object(av, "is_file_archived", return_value=False):
            assert av.validate_s3_files_exist("bkt", LOCATION, [
                AssetFileVersionItemModel(relativeKey="//cloud.las", versionId="v1")]) == []
        assert client.head_object.call_args.kwargs["Key"] == EMPTY_SEGMENT_FILE


@pytest.mark.unit
class TestTheFileListNamesAnEmptySegmentKeyByItsOwnPath:
    """#1977: the file list names 'A1//cloud.las' at '//cloud.las', the path that names it on the file routes,
    and 'A1/cloud.las' at '/cloud.las'; before, both were listed at '/cloud.las'."""

    @pytest.mark.parametrize("basic_mode", [True, False])
    def test_both_files_are_listed_at_their_own_paths(self, af, basic_mode):
        listing = af.list_s3_objects_with_archive_status(BUCKET, LOCATION, {}, basic_mode=basic_mode)
        paths = {item["key"]: item["relativePath"] for item in listing["items"]}
        assert paths[EMPTY_SEGMENT_FILE] == "//cloud.las"
        assert paths[ROOT_FILE] == "/cloud.las"
        assert paths[SUB_FILE] == "/A1/cloud.las"
        for key, path in paths.items():
            assert af.resolve_asset_file_path(LOCATION, path) == key

    def test_an_archived_empty_segment_key_is_listed_at_its_own_path(self, af):
        af.test_client.delete_object(Bucket=BUCKET, Key=EMPTY_SEGMENT_FILE)
        listing = af.list_s3_objects_with_archive_status(BUCKET, LOCATION, {}, include_archived=True)
        archived = [item for item in listing["items"] if item["key"] == EMPTY_SEGMENT_FILE]
        assert [item["relativePath"] for item in archived] == ["//cloud.las"]

    def test_a_folder_filter_lists_paths_relative_to_the_filter_as_before(self, af):
        af.test_client.put_object(Bucket=BUCKET, Key="A1/sub/x.las", Body=b"x")
        af.test_client.put_object(Bucket=BUCKET, Key="A1/sub//y.las", Body=b"x")
        listing = af.list_s3_objects_with_archive_status(BUCKET, LOCATION, {"prefix": "sub/"}, basic_mode=True)
        assert sorted(item["relativePath"] for item in listing["items"]) == ["//y.las", "/x.las"]

    @pytest.mark.parametrize("prefix_filter, expected", [
        ("//", ["/cloud.las"]),
        ("/", ["/A1/cloud.las", "/A1/other.las", "//cloud.las", "/cloud.las"]),
        ("/A1/", ["/cloud.las", "/other.las"]),
        ("A1/", ["/cloud.las", "/other.las"]),
    ])
    def test_the_prefix_filter_drops_one_leading_slash(self, af, prefix_filter, expected):
        """L5: '--prefix //' lists the 'A1//' folder (its paths relative to the filter, as for any folder
        filter); it stripped every '/' and listed the whole asset."""
        listing = af.list_s3_objects_with_archive_status(BUCKET, LOCATION, {"prefix": prefix_filter}, basic_mode=True)
        assert sorted(item["relativePath"] for item in listing["items"]) == sorted(expected)

    def test_a_single_file_assets_prefix_filter_drops_one_leading_slash(self, af):
        for key in ("p/model.glb", "p/model.glb/x.bin", "p/model.glb//y.bin"):
            af.test_client.put_object(Bucket=BUCKET, Key=key, Body=b"x")
        listing = af.list_s3_objects_with_archive_status(BUCKET, "p/model.glb", {"prefix": "//"}, basic_mode=True)
        assert [item["key"] for item in listing["items"]] == ["p/model.glb//y.bin"]

    @pytest.mark.parametrize("prefix,key,expected", [
        ("A1/", "A1/x", "/x"), ("A1/", "A1//x", "//x"), ("A1/", "A1/", "/"),
        ("A1/sub", "A1/sub/x", "/x"), ("A1/sub", "A1/subway.glb", "/way.glb"),
    ])
    def test_the_listed_path_rule(self, af, prefix, key, expected):
        assert af.listed_relative_path(prefix, key) == expected
