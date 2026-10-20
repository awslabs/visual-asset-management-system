# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Creating or reverting an asset version records the new version as the asset row's latest change:
the row written back carries `lastChangeSource: assetVersion` and a fresh `lastChangeAt`, keeps the
`lastUploadAt` of the last user upload, and drops `lastChangeWorkflowExecutionId`. That row write is
the stream MODIFY the compliance trigger evaluates on, so a version-coverage verdict follows the new
version rather than waiting for the next unrelated change."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# Module-scope import so the real `backend.backend.handlers` package is in sys.modules before the
# root conftest's autouse fixture installs its non-package placeholder (see S27-TEST-001).
from backend.backend.handlers.assets import assetVersions as _assetVersions

DB = "db1"
ASSET = "asset-1"
LAST_UPLOAD_AT = "2026-03-01T12:00:00+00:00"


def _asset():
    return {
        "databaseId": DB, "assetId": ASSET, "assetName": "N", "description": "desc",
        "bucketId": "bucket-1", "assetLocation": {"Key": f"{ASSET}/"}, "currentVersionId": "5",
        "lastChangeSource": "workflowExecution", "lastChangeAt": LAST_UPLOAD_AT,
        "lastChangeWorkflowExecutionId": "exec-1", "lastUploadAt": LAST_UPLOAD_AT,
    }


def _written_row(runner):
    av = _assetVersions
    asset_table = MagicMock(name="asset_table")
    with patch.object(av, "asset_table", asset_table), \
            patch.object(av, "get_asset_with_permissions", side_effect=lambda *a, **k: _asset()), \
            patch.object(av, "get_asset_s3_location", return_value=("asset-bucket", f"{ASSET}/")), \
            patch.object(av, "list_s3_files_with_versions", return_value=[{
                "relativeKey": "model.glb", "versionId": "s3v1", "size": 1,
                "lastModified": "2026-01-01T00:00:00", "etag": "e"}]), \
            patch.object(av, "reserve_next_asset_version", return_value="6"), \
            patch.object(av, "get_asset_version_metadata", return_value={"assetVersionId": "2"}), \
            patch.object(av, "get_asset_file_versions", return_value={"files": []}), \
            patch.object(av, "save_asset_file_versions", return_value=True), \
            patch.object(av, "save_asset_metadata_version", return_value=True), \
            patch.object(av, "mark_assetVersion_as_current", return_value=True), \
            patch.object(av, "send_subscription_email"):
        runner(av)
    assert asset_table.put_item.call_count == 1
    return asset_table.put_item.call_args.kwargs["Item"]


def _create(av):
    request = MagicMock(useLatestFiles=True, files=[], comment="c", versionAlias=None)
    av.create_asset_version(DB, ASSET, request, {"tokens": ["alice"]})


def _revert(av):
    request = MagicMock(assetVersionId="2", revertMetadata=False, comment="c")
    av.revert_asset_version(DB, ASSET, request, {"tokens": ["alice"]})


@pytest.mark.unit
@pytest.mark.parametrize("runner", [_create, _revert], ids=["create", "revert"])
class TestAssetVersionProvenance:

    def test_the_row_records_the_version_as_its_latest_change(self, runner):
        row = _written_row(runner)
        assert row["currentVersionId"] == "6"
        assert row["lastChangeSource"] == "assetVersion"
        assert datetime.fromisoformat(row["lastChangeAt"]) > datetime.fromisoformat(LAST_UPLOAD_AT)

    def test_the_last_upload_instant_is_left_as_it_is(self, runner):
        assert _written_row(runner)["lastUploadAt"] == LAST_UPLOAD_AT

    def test_a_previous_workflow_execution_id_is_dropped(self, runner):
        assert "lastChangeWorkflowExecutionId" not in _written_row(runner)


# S3 calls that write an object (and so raise an S3 event the file indexer relays to the compliance
# trigger).
S3_WRITE_CALLS = {"put_object", "copy_object", "copy", "upload_file", "upload_fileobj",
                  "create_multipart_upload", "upload_part_copy", "complete_multipart_upload"}


def _record_writes(runner, target_files=()):
    """Run `runner` and return, in order, the object writes it made (`("copy", instant)`), the asset
    row it wrote (`("stamp", lastChangeAt)`), and every S3 client / resource call it made."""
    av = _assetVersions
    events = []
    asset_table = MagicMock(name="asset_table")
    asset_table.put_item.side_effect = lambda **kwargs: events.append(
        ("stamp", kwargs["Item"]["lastChangeAt"]))

    def _copy(source_bucket, source_key, source_version_id, dest_bucket, dest_key):
        events.append(("copy", datetime.now(timezone.utc).isoformat()))
        return f"new-{source_version_id}"

    s3_client = MagicMock(name="s3_client")
    s3_resource = MagicMock(name="s3_resource")
    with patch.object(av, "asset_table", asset_table), \
            patch.object(av, "s3_client", s3_client), \
            patch.object(av, "s3_resource", s3_resource), \
            patch.object(av, "copy_s3_object_version", side_effect=_copy) as copy, \
            patch.object(av, "does_file_version_exist", return_value=True), \
            patch.object(av, "delete_assetAuxiliary_files"), \
            patch.object(av, "get_asset_with_permissions", side_effect=lambda *a, **k: _asset()), \
            patch.object(av, "get_asset_s3_location", return_value=("asset-bucket", f"{ASSET}/")), \
            patch.object(av, "list_s3_files_with_versions", return_value=[{
                "relativeKey": "model.glb", "versionId": "s3v1", "size": 1,
                "lastModified": "2026-01-01T00:00:00", "etag": "e"}]), \
            patch.object(av, "reserve_next_asset_version", return_value="6"), \
            patch.object(av, "get_asset_version_metadata", return_value={"assetVersionId": "2"}), \
            patch.object(av, "get_asset_file_versions", return_value={"files": list(target_files)}), \
            patch.object(av, "save_asset_file_versions", return_value=True), \
            patch.object(av, "save_asset_metadata_version", return_value=True), \
            patch.object(av, "mark_assetVersion_as_current", return_value=True), \
            patch.object(av, "send_subscription_email"):
        runner(av)
    s3_calls = {name.split(".")[0] for name, _args, _kwargs in s3_client.method_calls}
    s3_calls |= {name.split(".")[-1] for name, _args, _kwargs in s3_resource.mock_calls if name}
    return events, s3_calls, copy


@pytest.mark.unit
class TestTheStampFollowsEveryObjectTheVersionWrote:
    """The compliance trigger judges the object a file event names against an `assetVersion` stamp's
    `lastChangeAt` (`complianceTrigger.file_event_completion_time`), which holds only because the
    stamp is written after every object the version operation wrote: a revert copies the target
    version's objects and then stamps the row; a create writes no object at all, so no file event
    follows it and its stamp's stream MODIFY is the one evaluation it starts."""

    TARGET_FILES = [
        {"relativeKey": f"/r-{n}.txt", "versionId": f"s3v{n}", "size": 1, "etag": "e"}
        for n in (1, 2, 3)]

    def test_a_create_writes_no_s3_object(self):
        events, s3_calls, copy = _record_writes(_create)
        copy.assert_not_called()
        assert not s3_calls & S3_WRITE_CALLS
        assert [kind for kind, _ in events] == ["stamp"]

    def test_a_revert_stamps_the_row_after_its_last_copy(self):
        events, _s3_calls, copy = _record_writes(_revert, self.TARGET_FILES)
        assert copy.call_count == 3
        assert [kind for kind, _ in events] == ["copy", "copy", "copy", "stamp"]
        stamp = datetime.fromisoformat(events[-1][1])
        assert all(datetime.fromisoformat(at) <= stamp for kind, at in events if kind == "copy")
