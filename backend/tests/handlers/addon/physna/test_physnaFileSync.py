# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from unittest.mock import MagicMock, patch

# Module-level import ensures the real `backend.backend.handlers` package is
# populated in sys.modules before the root conftest's autouse fixture runs,
# preventing it from stubbing the package with a MockModule.
from backend.backend.handlers.addon.physna import physnaFileSync as _pfs  # noqa: F401


def _sns_sqs_event(s3_records):
    """Wrap a list of S3 event records as an SQS message containing an SNS notification."""
    sns_message = {"Records": s3_records, "ASSET_BUCKET_NAME": "test-bucket"}
    return {
        "Records": [
            {
                "eventSource": "aws:sqs",
                "body": json.dumps({"Type": "Notification", "Message": json.dumps(sns_message)}),
            }
        ]
    }


def _s3_put_record(bucket, key, event_name="ObjectCreated:Put"):
    return {
        "eventSource": "aws:s3",
        "eventName": event_name,
        "s3": {"bucket": {"name": bucket}, "object": {"key": key}},
    }


@pytest.mark.unit
class TestFileUpload:
    def test_unsupported_extension_is_skipped(self):
        from backend.backend.handlers.addon.physna import physnaFileSync

        event = _sns_sqs_event([_s3_put_record("bucket-1", "prefix/db-1/asset-1/notes.txt")])

        with patch.object(physnaFileSync, "_upload_file_to_physna") as upload:
            response = physnaFileSync.lambda_handler(event, MagicMock())

        assert response["statusCode"] == 200
        assert upload.call_count == 0

    def test_supported_file_triggers_upload(self):
        from backend.backend.handlers.addon.physna import physnaFileSync

        event = _sns_sqs_event([_s3_put_record("bucket-1", "prefix/db-1/asset-1/part.step")])

        with patch.object(physnaFileSync, "_resolve_asset_from_s3_event") as resolve, \
             patch.object(physnaFileSync, "_upload_file_to_physna") as upload:
            resolve.return_value = {
                "databaseId": "db-1",
                "assetId": "asset-1",
                "relativePath": "/part.step",
                "bucketName": "bucket-1",
                "s3Key": "prefix/db-1/asset-1/part.step",
            }
            response = physnaFileSync.lambda_handler(event, MagicMock())

        assert response["statusCode"] == 200
        upload.assert_called_once()
        call_kwargs = upload.call_args.kwargs or {}
        call_args = upload.call_args.args
        assert call_args[0] == "db-1"
        assert call_args[1] == "asset-1"
        assert call_args[2] == "/part.step"


@pytest.mark.unit
class TestShouldSkipS3Key:
    """Exclusion of reserved folders must match whole path segments, not
    filename prefixes. A base file named e.g. `preview.jpg` shares a prefix
    with the reserved `preview` folder but must NOT be excluded.
    """

    def test_reserved_folder_segment_is_skipped(self):
        from backend.backend.handlers.addon.physna import physnaFileSync

        assert physnaFileSync._should_skip_s3_key("asset-1/preview/thumb.png") is True
        assert physnaFileSync._should_skip_s3_key("asset-1/pipelines/foo/out.step") is True
        assert physnaFileSync._should_skip_s3_key("asset-1/temp-upload/part.step") is True

    def test_filename_sharing_reserved_prefix_is_not_skipped(self):
        from backend.backend.handlers.addon.physna import physnaFileSync

        # Regression: "preview.jpg"/"preview.png" begin with "preview" but are
        # base filenames, not the reserved "preview/" folder, so must be kept.
        assert physnaFileSync._should_skip_s3_key("asset-1/preview.jpg") is False
        assert physnaFileSync._should_skip_s3_key("asset-1/preview.png") is False
        assert physnaFileSync._should_skip_s3_key("asset-1/pipelines.step") is False
        assert physnaFileSync._should_skip_s3_key("asset-1/workspaceModel.glb") is False

    def test_previewfile_pattern_is_still_skipped(self):
        from backend.backend.handlers.addon.physna import physnaFileSync

        # The ".previewFile." pattern is a wildcard (*.previewFile.*): any key
        # CONTAINING the literal substring ".previewFile." is excluded, regardless
        # of base filename or extension.
        assert physnaFileSync._should_skip_s3_key("asset-1/part.step.previewFile.png") is True
        assert physnaFileSync._should_skip_s3_key("asset-1/photo.e57.previewFile.gif") is True
        assert physnaFileSync._should_skip_s3_key("asset-1/sub/dir/model.obj.previewFile.jpg") is True
        # Pattern can appear anywhere in the key, not only at the end.
        assert physnaFileSync._should_skip_s3_key("asset-1/x.previewFile.png.bak") is True

    def test_previewfile_pattern_is_substring_not_literal_star(self):
        from backend.backend.handlers.addon.physna import physnaFileSync

        # The "*" in "*.previewFile.*" denotes a wildcard, NOT a literal asterisk.
        # A file that merely resembles the word but lacks the exact ".previewFile."
        # substring must NOT be excluded, and a literal "*" is not required.
        assert physnaFileSync._should_skip_s3_key("asset-1/part.previewFilexpng") is False
        assert physnaFileSync._should_skip_s3_key("asset-1/previewFile.png") is False  # no leading dot
        assert physnaFileSync._should_skip_s3_key("asset-1/my.preview.png") is False

    def test_folder_marker_is_skipped(self):
        from backend.backend.handlers.addon.physna import physnaFileSync

        assert physnaFileSync._should_skip_s3_key("asset-1/preview/") is True


@pytest.mark.unit
class TestSkipLogging:
    """Every skip path that drops a record must emit an INFO log line so the
    skip is visible in CloudWatch (no silent drops). Tests patch the module
    logger directly since safeLogger does not route through stdlib logging.
    """

    def test_unsupported_extension_in_s3_record_is_logged(self):
        from backend.backend.handlers.addon.physna import physnaFileSync

        # `.ifc` is a format Physna rejects — neither synced nor viewable.
        s3_record = _s3_put_record("bucket-1", "prefix/db-1/asset-1/model.ifc")

        with patch.object(physnaFileSync, "_resolve_asset_from_s3_event") as resolve, \
             patch.object(physnaFileSync.logger, "info") as log_info:
            resolve.return_value = {
                "databaseId": "db-1",
                "assetId": "asset-1",
                "relativePath": "/model.ifc",
                "bucketName": "bucket-1",
                "s3Key": "prefix/db-1/asset-1/model.ifc",
            }
            ok = physnaFileSync._handle_s3_record(s3_record)

        assert ok is True
        log_messages = " ".join(str(c.args[0]) for c in log_info.call_args_list)
        assert "unsupported file type" in log_messages.lower()
        assert "/model.ifc" in log_messages

    def test_excluded_prefix_in_s3_record_is_logged(self):
        from backend.backend.handlers.addon.physna import physnaFileSync

        # Key starts with the excluded "pipelines/" prefix
        s3_record = _s3_put_record(
            "bucket-1", "prefix/db-1/asset-1/pipelines/foo/out.step"
        )

        with patch.object(physnaFileSync.logger, "info") as log_info:
            ok = physnaFileSync._handle_s3_record(s3_record)

        assert ok is True
        log_messages = " ".join(str(c.args[0]) for c in log_info.call_args_list)
        assert "excluded path" in log_messages.lower()

    def test_unsupported_extension_in_metadata_stream_is_logged(self):
        from backend.backend.handlers.addon.physna import physnaFileSync

        # `.ifc` is a format Physna rejects — neither synced nor viewable.
        stream_record = {
            "eventSource": "aws:dynamodb",
            "eventName": "MODIFY",
            "dynamodb": {
                "Keys": {
                    "metadataKey": {"S": "foo"},
                    "databaseId:assetId:filePath": {
                        "S": "db-1:asset-1:/model.ifc"
                    },
                },
                "NewImage": {
                    "databaseId:assetId:filePath": {
                        "S": "db-1:asset-1:/model.ifc"
                    },
                },
            },
        }

        with patch.object(physnaFileSync.logger, "info") as log_info:
            ok = physnaFileSync._handle_file_metadata_stream(stream_record)

        assert ok is True
        log_messages = " ".join(str(c.args[0]) for c in log_info.call_args_list)
        assert "unsupported file type" in log_messages.lower()
        assert "/model.ifc" in log_messages


@pytest.mark.unit
class TestUpdateMetadataFullReplace:
    """_update_physna_metadata performs a FULL REPLACE: keys that are in
    Physna but NOT in the new payload are DELETEed before the PATCH, so
    VAMS-side metadata removals propagate to Physna.
    """

    def test_prunes_stale_keys_then_patches_remaining(self):
        from backend.backend.handlers.addon.physna import (
            physnaFileSync,
            physnaCommon,
        )

        client = MagicMock()

        with patch.object(physnaFileSync, "ensure_metadata_fields_registered") as ensure, \
             patch.object(physnaFileSync, "get_physna_asset") as get_asset, \
             patch.object(physnaFileSync, "delete_physna_metadata_fields") as del_fields:

            get_asset.return_value = {
                "id": "uuid-1",
                "path": "db-1/asset-1/part.step",
                "metadata": {"old1": "x", "old2": "y", "shared": "keep"},
                "state": "indexing",
            }

            patch_response = MagicMock()
            patch_response.status = 200
            client.request.return_value = patch_response

            ok = physnaFileSync._update_physna_metadata(
                client,
                "db-1/asset-1/part.step",
                "uuid-1",
                {"shared": "updated", "fresh": "new"},
            )

        assert ok is True
        ensure.assert_called_once()
        # Stale keys ("old1", "old2") must be deleted; "shared" must NOT be
        # deleted because it still exists in the payload.
        del_fields.assert_called_once()
        _client, _tenant, _uuid, to_delete = del_fields.call_args.args
        assert sorted(to_delete) == ["old1", "old2"]

        # PATCH body must contain the NEW payload only (not the old keys).
        patch_calls = [c for c in client.request.call_args_list if c.args[0] == "PATCH"]
        assert len(patch_calls) == 1
        import json as _json

        body = _json.loads(patch_calls[0].kwargs["body"].decode("utf-8"))
        assert body == {"metadata": {"shared": "updated", "fresh": "new"}}

    def test_no_prune_when_nothing_to_remove(self):
        from backend.backend.handlers.addon.physna import physnaFileSync

        client = MagicMock()

        with patch.object(physnaFileSync, "ensure_metadata_fields_registered"), \
             patch.object(physnaFileSync, "get_physna_asset") as get_asset, \
             patch.object(physnaFileSync, "delete_physna_metadata_fields") as del_fields:

            get_asset.return_value = {
                "metadata": {"a": "1"},
            }
            patch_response = MagicMock()
            patch_response.status = 204
            client.request.return_value = patch_response

            physnaFileSync._update_physna_metadata(
                client, "path", "uuid-1", {"a": "1", "b": "2"}
            )

        del_fields.assert_not_called()


@pytest.mark.unit
class TestBatchIsolation:
    """Every record in the SQS batch must be processed independently. A
    failure on one upload must not cause the remaining records to be
    silently dropped."""

    def test_single_failure_does_not_abort_remaining_records(self):
        from backend.backend.handlers.addon.physna import physnaFileSync

        records = [
            _s3_put_record("bucket-1", "prefix/db-1/asset-1/a.step"),
            _s3_put_record("bucket-1", "prefix/db-1/asset-1/b.step"),
            _s3_put_record("bucket-1", "prefix/db-1/asset-1/c.step"),
        ]
        event = _sns_sqs_event(records)

        call_keys = []

        def side_effect(record):
            key = record.get("s3", {}).get("object", {}).get("key")
            call_keys.append(key)
            if "b.step" in key:
                raise RuntimeError("boom — Physna rejected this one")
            return True

        with patch.object(
            physnaFileSync, "_handle_s3_record", side_effect=side_effect
        ):
            response = physnaFileSync.lambda_handler(event, MagicMock())

        assert response["statusCode"] == 200
        # All three records were ATTEMPTED — the middle failure did not stop
        # the loop.
        assert len(call_keys) == 3
        assert sorted(call_keys) == sorted(
            [
                "prefix/db-1/asset-1/a.step",
                "prefix/db-1/asset-1/b.step",
                "prefix/db-1/asset-1/c.step",
            ]
        )
        # Only the two that succeeded count as processed
        assert response["body"]["successful"] == 2

    def test_multiple_s3_records_in_one_sns_all_processed(self):
        """A common VAMS upload batch: user uploads 3 files, sqsBucketSync
        emits them in a single SNS notification. All must be attempted."""
        from backend.backend.handlers.addon.physna import physnaFileSync

        event = _sns_sqs_event(
            [
                _s3_put_record("bucket-1", "prefix/db-1/asset-1/a.step"),
                _s3_put_record("bucket-1", "prefix/db-1/asset-1/b.step"),
                _s3_put_record("bucket-1", "prefix/db-1/asset-1/c.step"),
            ]
        )

        processed = []

        def side_effect(record):
            processed.append(record["s3"]["object"]["key"])
            return True

        with patch.object(
            physnaFileSync, "_handle_s3_record", side_effect=side_effect
        ):
            response = physnaFileSync.lambda_handler(event, MagicMock())

        assert response["statusCode"] == 200
        assert len(processed) == 3
        assert response["body"]["successful"] == 3


@pytest.mark.unit
class TestFileVersionTracking:
    """Upload flow respects the __VAMS__FileVersion tracking key:
      - If Physna already has the same S3 VersionId, skip the upload.
      - If Physna has a different version, DELETE then re-upload.
      - On fresh upload, __VAMS__FileVersion is set to the current S3 VersionId.
    """

    def _setup_upload_mocks(self, monkeypatch, *, s3_version, physna_state):
        """Wire the helpers _upload_file_to_physna depends on.

        physna_state: None | {"uuid": str, "file_version": str | None}
        """
        from backend.backend.handlers.addon.physna import physnaFileSync

        # S3 version lookup
        monkeypatch.setattr(
            physnaFileSync, "_get_s3_version_id", lambda b, k: s3_version
        )

        # Existing Physna asset lookup
        existing_uuid = physna_state["uuid"] if physna_state else None
        monkeypatch.setattr(
            physnaFileSync,
            "lookup_physna_asset_id",
            lambda c, t, p: existing_uuid,
        )
        if physna_state is not None:
            monkeypatch.setattr(
                physnaFileSync,
                "get_physna_asset",
                lambda c, t, u: {
                    "id": physna_state["uuid"],
                    "metadata": (
                        {"__VAMS__FileVersion": physna_state["file_version"]}
                        if physna_state["file_version"] is not None
                        else {}
                    ),
                },
            )
        else:
            monkeypatch.setattr(
                physnaFileSync, "get_physna_asset", lambda c, t, u: None
            )

        # Metadata payload building (return a fixed payload for inspection)
        monkeypatch.setattr(
            physnaFileSync,
            "_build_metadata_payload",
            lambda db, aid, rel, file_version, asset_details=None: {
                "user_key": "v",
                **(
                    {"__VAMS__FileVersion": file_version}
                    if file_version is not None
                    else {}
                ),
            },
        )
        return physnaFileSync

    def test_skip_upload_when_file_version_matches(self, monkeypatch):
        physnaFileSync = self._setup_upload_mocks(
            monkeypatch,
            s3_version="v-abc",
            physna_state={"uuid": "uuid-1", "file_version": "v-abc"},
        )

        client = MagicMock()

        with patch.object(physnaFileSync, "_delete_physna_asset_by_uuid") as delete_stale, \
             patch.object(physnaFileSync, "_update_physna_metadata") as update_meta, \
             patch.object(physnaFileSync._s3, "download_file") as s3_download:

            ok = physnaFileSync._upload_file_to_physna(
                "db-1", "asset-1", "/part.step", "bucket", "bucket/key", client=client
            )

        assert ok is True
        # No upload, no delete — just a metadata refresh
        s3_download.assert_not_called()
        delete_stale.assert_not_called()
        update_meta.assert_called_once()
        # Metadata refresh path uses file_version=None (preserve existing)
        _client, _fp, uuid_arg, payload = update_meta.call_args.args
        assert uuid_arg == "uuid-1"
        assert "__VAMS__FileVersion" not in payload

    def test_delete_and_reupload_when_file_version_differs(self, monkeypatch):
        physnaFileSync = self._setup_upload_mocks(
            monkeypatch,
            s3_version="v-new",
            physna_state={"uuid": "uuid-1", "file_version": "v-old"},
        )

        client = MagicMock()
        upload_response = MagicMock()
        upload_response.status = 201
        upload_response.data = json.dumps({"id": "uuid-2"}).encode("utf-8")
        client.request.return_value = upload_response

        with patch.object(physnaFileSync, "_delete_physna_asset_by_uuid") as delete_stale, \
             patch.object(physnaFileSync, "_update_physna_metadata") as update_meta, \
             patch.object(physnaFileSync._s3, "download_file"), \
             patch("builtins.open", MagicMock()) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = b"stuff"

            ok = physnaFileSync._upload_file_to_physna(
                "db-1", "asset-1", "/part.step", "bucket", "bucket/key", client=client
            )

        assert ok is True
        delete_stale.assert_called_once()
        assert delete_stale.call_args.args[1] == "uuid-1"
        # The upload went out with the fresh VersionId
        update_meta.assert_called_once()
        _client, _fp, _uuid, payload = update_meta.call_args.args
        assert payload["__VAMS__FileVersion"] == "v-new"

    def test_metadata_stream_reuploads_when_file_version_tag_missing(
        self, monkeypatch
    ):
        """A metadata-only VAMS change on a file whose Physna copy has no
        __VAMS__FileVersion tag must route to an upload, not a metadata
        PATCH. Missing tag ⇒ treat as stale."""
        from backend.backend.handlers.addon.physna import physnaFileSync

        # Physna has the file but with no version tag
        monkeypatch.setattr(
            physnaFileSync,
            "lookup_physna_asset_id",
            lambda c, t, p: "uuid-1",
        )
        monkeypatch.setattr(
            physnaFileSync,
            "get_physna_asset",
            lambda c, t, u: {"id": "uuid-1", "metadata": {"user_key": "v"}},
        )
        monkeypatch.setattr(
            physnaFileSync,
            "get_asset_details",
            lambda db, aid: {
                "assetName": "My Asset",
                "bucketId": "b-1",
                "assetLocation": {"Key": "prefix/asset-1/"},
            },
        )
        monkeypatch.setattr(
            physnaFileSync,
            "get_bucket_details",
            lambda bid: {"bucketName": "bucket-1", "baseAssetsPrefix": "prefix/"},
        )
        monkeypatch.setattr(
            physnaFileSync, "is_sync_supported_file", lambda rel: True
        )

        stream_record = {
            "eventSource": "aws:dynamodb",
            "eventName": "MODIFY",
            "dynamodb": {
                "Keys": {
                    "metadataKey": {"S": "k"},
                    "databaseId:assetId:filePath": {
                        "S": "db-1:asset-1:/part.step"
                    },
                },
                "NewImage": {
                    "databaseId:assetId:filePath": {
                        "S": "db-1:asset-1:/part.step"
                    },
                },
            },
        }

        with patch.object(physnaFileSync, "_upload_file_to_physna") as upload, \
             patch.object(physnaFileSync, "_update_physna_metadata") as update_meta:
            upload.return_value = True
            physnaFileSync._handle_file_metadata_stream(stream_record)

        # Must have routed to upload, NOT to a metadata-only PATCH
        upload.assert_called_once()
        update_meta.assert_not_called()

    def test_fresh_upload_sets_file_version(self, monkeypatch):
        physnaFileSync = self._setup_upload_mocks(
            monkeypatch,
            s3_version="v-first",
            physna_state=None,  # not in Physna yet
        )

        client = MagicMock()
        upload_response = MagicMock()
        upload_response.status = 201
        upload_response.data = json.dumps({"id": "uuid-1"}).encode("utf-8")
        client.request.return_value = upload_response

        with patch.object(physnaFileSync, "_delete_physna_asset_by_uuid") as delete_stale, \
             patch.object(physnaFileSync, "_update_physna_metadata") as update_meta, \
             patch.object(physnaFileSync._s3, "download_file"), \
             patch("builtins.open", MagicMock()) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = b"stuff"

            ok = physnaFileSync._upload_file_to_physna(
                "db-1", "asset-1", "/part.step", "bucket", "bucket/key", client=client
            )

        assert ok is True
        delete_stale.assert_not_called()
        update_meta.assert_called_once()
        _client, _fp, _uuid, payload = update_meta.call_args.args
        assert payload["__VAMS__FileVersion"] == "v-first"


@pytest.mark.unit
class TestS3MissingObjectReconciliation:
    """When the S3 object we're supposed to upload has already been deleted,
    the sync handler must reconcile Physna (delete the stale copy if present)
    and return successfully — not raise, which would trigger a doomed SQS
    retry that keeps hitting the same NoSuchKey.
    """

    def _setup(self, monkeypatch, *, physna_has_asset: bool):
        from backend.backend.handlers.addon.physna import physnaFileSync

        monkeypatch.setattr(
            physnaFileSync, "_get_s3_version_id", lambda b, k: None
        )
        monkeypatch.setattr(
            physnaFileSync,
            "lookup_physna_asset_id",
            lambda c, t, p: "uuid-stale" if physna_has_asset else None,
        )
        monkeypatch.setattr(
            physnaFileSync,
            "get_physna_asset",
            lambda c, t, u: (
                {"id": "uuid-stale", "metadata": {"__VAMS__FileVersion": "v-old"}}
                if physna_has_asset
                else None
            ),
        )
        monkeypatch.setattr(
            physnaFileSync,
            "_build_metadata_payload",
            lambda db, aid, rel, file_version, asset_details=None: {},
        )
        return physnaFileSync

    @staticmethod
    def _no_such_key_error():
        from botocore.exceptions import ClientError

        return ClientError(
            error_response={
                "Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            operation_name="HeadObject",
        )

    def test_missing_s3_object_deletes_stale_physna_copy(self, monkeypatch):
        """The canonical case: VAMS event fires for an S3 key whose object
        has already been removed, but Physna still has a copy from a prior
        upload. The handler must issue a DELETE against Physna so the two
        sides converge, then return True so the batch record is ack'd."""
        physnaFileSync = self._setup(monkeypatch, physna_has_asset=True)
        client = MagicMock()

        with patch.object(
            physnaFileSync._s3,
            "download_file",
            side_effect=self._no_such_key_error(),
        ), patch.object(
            physnaFileSync, "_delete_physna_asset_by_uuid"
        ) as reconcile_delete, patch.object(
            physnaFileSync, "_update_physna_metadata"
        ) as update_meta:
            ok = physnaFileSync._upload_file_to_physna(
                "db-1", "asset-1", "/gone.step", "bucket", "bucket/key", client=client
            )

        assert ok is True
        reconcile_delete.assert_called_once()
        # We bailed out before the metadata PATCH — nothing to update with.
        update_meta.assert_not_called()

    def test_missing_s3_object_with_no_physna_copy_is_a_clean_noop(self, monkeypatch):
        """If S3 and Physna are both empty for this path we simply ack the
        event — there is nothing to reconcile."""
        physnaFileSync = self._setup(monkeypatch, physna_has_asset=False)
        client = MagicMock()

        with patch.object(
            physnaFileSync._s3,
            "download_file",
            side_effect=self._no_such_key_error(),
        ), patch.object(
            physnaFileSync, "_delete_physna_asset_by_uuid"
        ) as reconcile_delete, patch.object(
            physnaFileSync, "_update_physna_metadata"
        ) as update_meta:
            ok = physnaFileSync._upload_file_to_physna(
                "db-1", "asset-1", "/gone.step", "bucket", "bucket/key", client=client
            )

        assert ok is True
        reconcile_delete.assert_not_called()
        update_meta.assert_not_called()

    def test_unrelated_s3_error_still_raises(self, monkeypatch):
        """Non-404 S3 errors (auth failure, throttling, etc.) must NOT be
        silently swallowed — those are retryable infrastructure problems
        and the SQS retry is the right recovery."""
        from botocore.exceptions import ClientError

        physnaFileSync = self._setup(monkeypatch, physna_has_asset=True)
        client = MagicMock()

        access_denied = ClientError(
            error_response={
                "Error": {"Code": "AccessDenied", "Message": "nope"},
                "ResponseMetadata": {"HTTPStatusCode": 403},
            },
            operation_name="GetObject",
        )

        with patch.object(
            physnaFileSync._s3, "download_file", side_effect=access_denied
        ), patch.object(
            physnaFileSync, "_delete_physna_asset_by_uuid"
        ) as reconcile_delete:
            with pytest.raises(ClientError):
                physnaFileSync._upload_file_to_physna(
                    "db-1",
                    "asset-1",
                    "/part.step",
                    "bucket",
                    "bucket/key",
                    client=client,
                )

        reconcile_delete.assert_not_called()


@pytest.mark.unit
class TestDeletePhysnaAssetS3Guard:
    """_delete_physna_asset must not issue a Physna DELETE while the VAMS
    S3 object is still present. This blocks the class of regressions
    where a metadata-row change (or any non-S3-ObjectRemoved event)
    misroutes into the delete path and erases live Physna data.
    """

    def _call_delete(self, physnaFileSync, *, skip=False):
        client = MagicMock()
        physnaFileSync._delete_physna_asset(
            client,
            "db-1",
            "asset-1",
            "/part.step",
            skip_s3_existence_check=skip,
        )
        return client

    def test_skips_physna_delete_when_s3_object_still_present(self, monkeypatch):
        """The regression scenario: S3 object is still there. The lookup
        and DELETE against Physna must not run at all."""
        from backend.backend.handlers.addon.physna import physnaFileSync

        monkeypatch.setattr(
            physnaFileSync, "_s3_object_still_exists", lambda db, a, r: True
        )
        with patch.object(
            physnaFileSync, "lookup_physna_asset_id"
        ) as lookup:
            client = self._call_delete(physnaFileSync)
        lookup.assert_not_called()
        # No requests ever sent.
        client.request.assert_not_called()

    def test_skips_physna_delete_when_s3_check_is_inconclusive(self, monkeypatch):
        """If the S3 check can't determine presence (e.g. bucket not
        resolvable) we must err on the side of preservation and NOT
        delete — a follow-up event or manual reconcile will pick it up."""
        from backend.backend.handlers.addon.physna import physnaFileSync

        monkeypatch.setattr(
            physnaFileSync, "_s3_object_still_exists", lambda db, a, r: None
        )
        with patch.object(
            physnaFileSync, "lookup_physna_asset_id"
        ) as lookup:
            client = self._call_delete(physnaFileSync)
        lookup.assert_not_called()
        client.request.assert_not_called()

    def test_proceeds_with_delete_when_s3_object_is_gone(self, monkeypatch):
        """Happy path: S3 says the object is genuinely gone, so Physna
        must be reconciled."""
        from backend.backend.handlers.addon.physna import physnaFileSync

        monkeypatch.setattr(
            physnaFileSync, "_s3_object_still_exists", lambda db, a, r: False
        )
        # Skip folder cleanup — not part of this test's contract.
        monkeypatch.setattr(
            physnaFileSync, "delete_folder_if_empty", lambda *a, **kw: None
        )

        client = MagicMock()
        delete_response = MagicMock()
        delete_response.status = 204
        delete_response.data = b""
        client.request.return_value = delete_response

        with patch.object(
            physnaFileSync,
            "lookup_physna_asset_id",
            return_value="uuid-123",
        ):
            physnaFileSync._delete_physna_asset(
                client, "db-1", "asset-1", "/part.step"
            )

        # DELETE was issued against the resolved UUID.
        assert client.request.call_count == 1
        method, url = client.request.call_args.args[0:2]
        assert method == "DELETE"
        assert "uuid-123" in url

    def test_skip_flag_bypasses_s3_check_for_authoritative_callers(
        self, monkeypatch
    ):
        """_handle_s3_record on an ObjectRemoved event uses the skip flag
        because S3 itself told us the file is gone — we must not waste a
        head_object round-trip that could fail transiently."""
        from backend.backend.handlers.addon.physna import physnaFileSync

        # Rigged so that if the guard were consulted it would BLOCK the
        # delete. With skip=True it must not even be consulted.
        monkeypatch.setattr(
            physnaFileSync,
            "_s3_object_still_exists",
            lambda db, a, r: (_ for _ in ()).throw(
                AssertionError("guard must not be called when skipped")
            ),
        )
        monkeypatch.setattr(
            physnaFileSync, "delete_folder_if_empty", lambda *a, **kw: None
        )

        client = MagicMock()
        delete_response = MagicMock()
        delete_response.status = 204
        delete_response.data = b""
        client.request.return_value = delete_response

        with patch.object(
            physnaFileSync,
            "lookup_physna_asset_id",
            return_value="uuid-123",
        ):
            physnaFileSync._delete_physna_asset(
                client, "db-1", "asset-1", "/part.step",
                skip_s3_existence_check=True,
            )

        assert client.request.call_count == 1


@pytest.mark.unit
class TestS3ObjectStillExists:
    """_s3_object_still_exists is the S3-side truth source used by the
    delete guard. It must return a definitive True / False when it can,
    and None when it cannot, so callers can distinguish "safe to delete"
    from "don't know — preserve"."""

    def _setup(
        self,
        monkeypatch,
        *,
        asset=None,
        bucket=None,
        head_result=None,
    ):
        from backend.backend.handlers.addon.physna import physnaFileSync

        monkeypatch.setattr(
            physnaFileSync, "get_asset_details", lambda db, a: asset
        )
        monkeypatch.setattr(
            physnaFileSync, "get_bucket_details", lambda bid: bucket
        )
        monkeypatch.setattr(
            physnaFileSync,
            "_head_object_with_encoding_fallback",
            lambda b, k: head_result,
        )
        return physnaFileSync

    def test_returns_false_when_vams_asset_record_is_gone(self, monkeypatch):
        pfs = self._setup(monkeypatch, asset=None)
        assert pfs._s3_object_still_exists("db", "a", "/p.step") is False

    def test_returns_none_when_bucket_cannot_be_resolved(self, monkeypatch):
        """Cannot safely decide either way — preserve Physna data."""
        pfs = self._setup(
            monkeypatch,
            asset={"bucketId": "b-1", "assetLocation": {}},
            bucket=None,
        )
        assert pfs._s3_object_still_exists("db", "a", "/p.step") is None

    def test_returns_true_when_head_object_succeeds(self, monkeypatch):
        pfs = self._setup(
            monkeypatch,
            asset={
                "bucketId": "b-1",
                "assetLocation": {"Key": "assets/asset-1/"},
            },
            bucket={
                "bucketName": "vams-bucket",
                "baseAssetsPrefix": "assets/",
            },
            head_result={"response": {"VersionId": "v1"}, "key": "assets/asset-1/p.step"},
        )
        assert pfs._s3_object_still_exists("db", "asset-1", "/p.step") is True

    def test_returns_false_when_head_object_404s(self, monkeypatch):
        pfs = self._setup(
            monkeypatch,
            asset={
                "bucketId": "b-1",
                "assetLocation": {"Key": "assets/asset-1/"},
            },
            bucket={
                "bucketName": "vams-bucket",
                "baseAssetsPrefix": "assets/",
            },
            head_result=None,
        )
        assert pfs._s3_object_still_exists("db", "asset-1", "/p.step") is False


@pytest.mark.unit
class TestResolveAssetFromS3KeyWithoutMetadata:
    """When an S3 object is deleted, head_object cannot read its user-
    metadata anymore — the metadata-free resolver derives the asset
    identifiers from the S3 key, the bucket registry (bucketNameGSI),
    and the assetStorageTable's assetIdGSI instead."""

    def _setup(
        self,
        monkeypatch,
        *,
        bucket_details=None,
        database_id=None,
    ):
        from backend.backend.handlers.addon.physna import physnaFileSync

        monkeypatch.setattr(
            physnaFileSync, "get_bucket_details_by_name", lambda n: bucket_details
        )
        # The resolver now passes bucket_name + base_assets_prefix for
        # disambiguation. Accept them as kwargs without caring what they
        # are — we just want the mock to return the test's scripted
        # databaseId regardless of disambiguation state.
        monkeypatch.setattr(
            physnaFileSync,
            "get_database_id_for_asset_id",
            lambda a, bucket_name=None, base_assets_prefix=None: database_id,
        )
        return physnaFileSync

    def test_resolves_when_bucket_has_no_prefix(self, monkeypatch):
        pfs = self._setup(
            monkeypatch,
            bucket_details={
                "bucketId": "b1",
                "bucketName": "vams-bucket",
                "baseAssetsPrefix": "",
            },
            database_id="db-1",
        )
        resolved = pfs._resolve_asset_from_s3_key_without_metadata(
            "vams-bucket", "xABC/ML2-Headset.glb"
        )
        assert resolved == {
            "databaseId": "db-1",
            "assetId": "xABC",
            "relativePath": "/ML2-Headset.glb",
            "bucketName": "vams-bucket",
            "s3Key": "xABC/ML2-Headset.glb",
        }

    def test_resolves_when_bucket_has_nonempty_prefix(self, monkeypatch):
        pfs = self._setup(
            monkeypatch,
            bucket_details={
                "bucketId": "b1",
                "bucketName": "vams-bucket",
                "baseAssetsPrefix": "vams/assets/",
            },
            database_id="db-1",
        )
        resolved = pfs._resolve_asset_from_s3_key_without_metadata(
            "vams-bucket", "vams/assets/xABC/sub/part.step"
        )
        assert resolved["assetId"] == "xABC"
        assert resolved["relativePath"] == "/sub/part.step"

    def test_resolves_real_world_s3_key_shape_from_incident(self, monkeypatch):
        """The exact key pattern from the incident log: prefix-less bucket
        and `{assetId}/{filename}` layout. Must produce a clean asset +
        relative-path split with no extra round trips."""
        pfs = self._setup(
            monkeypatch,
            bucket_details={
                "bucketId": "b1",
                "bucketName": "vams-core-prod5-us-west-2-stor-assetbucket1d025086-p1tjv26kvfgd",
                "baseAssetsPrefix": "",
            },
            database_id="headset-db",
        )
        resolved = pfs._resolve_asset_from_s3_key_without_metadata(
            "vams-core-prod5-us-west-2-stor-assetbucket1d025086-p1tjv26kvfgd",
            "x34e4c4b3-e4f1-41d3-8108-33f3a1a2b68e/ML2-Headset_1.2.1.glb",
        )
        assert resolved["databaseId"] == "headset-db"
        assert resolved["assetId"] == "x34e4c4b3-e4f1-41d3-8108-33f3a1a2b68e"
        assert resolved["relativePath"] == "/ML2-Headset_1.2.1.glb"

    def test_returns_none_when_bucket_not_registered(self, monkeypatch):
        pfs = self._setup(monkeypatch, bucket_details=None)
        assert (
            pfs._resolve_asset_from_s3_key_without_metadata(
                "unknown-bucket", "x/y.glb"
            )
            is None
        )

    def test_returns_none_when_database_lookup_fails(self, monkeypatch):
        """Orphan assetId with no VAMS record — don't fabricate a
        databaseId. Skipping is the right behavior."""
        pfs = self._setup(
            monkeypatch,
            bucket_details={
                "bucketId": "b1",
                "bucketName": "vams-bucket",
                "baseAssetsPrefix": "",
            },
            database_id=None,
        )
        assert (
            pfs._resolve_asset_from_s3_key_without_metadata(
                "vams-bucket", "xORPHAN/part.step"
            )
            is None
        )

    def test_returns_none_when_key_has_no_path_separator(self, monkeypatch):
        """A key like `something.glb` at the bucket root doesn't match the
        VAMS `{assetId}/...` layout — skip rather than guess."""
        pfs = self._setup(
            monkeypatch,
            bucket_details={
                "bucketId": "b1",
                "bucketName": "vams-bucket",
                "baseAssetsPrefix": "",
            },
            database_id="db-1",
        )
        assert (
            pfs._resolve_asset_from_s3_key_without_metadata(
                "vams-bucket", "just-a-file.glb"
            )
            is None
        )


@pytest.mark.unit
class TestObjectRemovedSkipsHeadObject:
    """S3 ObjectRemoved events must reach _delete_physna_asset even when
    head_object 404s — which is the norm, since the object is gone by the
    time the event fires. Before this fix, the head_object 404 aborted
    the resolver and the delete never ran."""

    def test_object_removed_triggers_delete_despite_head_object_404(
        self, monkeypatch
    ):
        from backend.backend.handlers.addon.physna import physnaFileSync

        # Rig the head_object helper to 404 — same as the incident log.
        monkeypatch.setattr(
            physnaFileSync,
            "_head_object_with_encoding_fallback",
            lambda bucket, key: None,
        )
        # Route the metadata-free resolver to return a concrete asset.
        monkeypatch.setattr(
            physnaFileSync,
            "_resolve_asset_from_s3_key_without_metadata",
            lambda bucket, key: {
                "databaseId": "db-1",
                "assetId": "xABC",
                "relativePath": "/part.glb",
                "bucketName": bucket,
                "s3Key": key,
            },
        )

        event = _sns_sqs_event(
            [_s3_put_record("vams-bucket", "xABC/part.glb", event_name="ObjectRemoved:Delete")]
        )

        with patch.object(
            physnaFileSync, "_delete_physna_asset"
        ) as do_delete, patch.object(
            physnaFileSync, "PhysnaClient"
        ):
            response = physnaFileSync.lambda_handler(event, MagicMock())

        assert response["statusCode"] == 200
        do_delete.assert_called_once()
        # And it was invoked with skip_s3_existence_check=True — S3 itself
        # is the authoritative signal, no point re-checking with head_object.
        assert (
            do_delete.call_args.kwargs.get("skip_s3_existence_check") is True
        )
        _client, db, asset, rel = do_delete.call_args.args
        assert db == "db-1"
        assert asset == "xABC"
        assert rel == "/part.glb"

    def test_delete_marker_created_event_also_triggers_physna_delete(
        self, monkeypatch
    ):
        """VAMS treats archive (soft delete, ``ObjectRemoved:
        DeleteMarkerCreated``) the same as permanent delete for Physna:
        the file is no longer accessible in VAMS, so the Physna copy
        should be removed. The handler's ``event_name.startswith
        ("ObjectRemoved")`` check must cover BOTH delete shapes."""
        from backend.backend.handlers.addon.physna import physnaFileSync

        monkeypatch.setattr(
            physnaFileSync,
            "_resolve_asset_from_s3_key_without_metadata",
            lambda bucket, key: {
                "databaseId": "db-1",
                "assetId": "xABC",
                "relativePath": "/archived.glb",
                "bucketName": bucket,
                "s3Key": key,
            },
        )

        event = _sns_sqs_event(
            [
                _s3_put_record(
                    "vams-bucket",
                    "xABC/archived.glb",
                    event_name="ObjectRemoved:DeleteMarkerCreated",
                )
            ]
        )

        with patch.object(
            physnaFileSync, "_delete_physna_asset"
        ) as do_delete, patch.object(
            physnaFileSync, "PhysnaClient"
        ):
            response = physnaFileSync.lambda_handler(event, MagicMock())

        assert response["statusCode"] == 200
        do_delete.assert_called_once()
        _client, db, asset, rel = do_delete.call_args.args
        assert db == "db-1"
        assert asset == "xABC"
        assert rel == "/archived.glb"

    def test_non_remove_events_still_use_head_object_resolver(
        self, monkeypatch
    ):
        """The metadata-free path is ONLY for ObjectRemoved — ObjectCreated
        etc. must still use head_object so we can read user-metadata and
        the canonical S3 key form."""
        from backend.backend.handlers.addon.physna import physnaFileSync

        called = {"metadata_free": 0, "metadata_backed": 0}

        def metadata_free_resolver(b, k):
            called["metadata_free"] += 1
            return None

        def metadata_backed_resolver(b, k):
            called["metadata_backed"] += 1
            return None  # trigger early exit; we just want to see which one runs

        monkeypatch.setattr(
            physnaFileSync,
            "_resolve_asset_from_s3_key_without_metadata",
            metadata_free_resolver,
        )
        monkeypatch.setattr(
            physnaFileSync,
            "_resolve_asset_from_s3_event",
            metadata_backed_resolver,
        )

        event = _sns_sqs_event(
            [_s3_put_record("vams-bucket", "xABC/part.glb", event_name="ObjectCreated:Put")]
        )

        with patch.object(physnaFileSync, "PhysnaClient"):
            physnaFileSync.lambda_handler(event, MagicMock())

        assert called["metadata_free"] == 0
        assert called["metadata_backed"] == 1
