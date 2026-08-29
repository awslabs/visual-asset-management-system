# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Physna upload path must not report a half-completed sync as a success.

``_upload_file_to_physna_impl`` does two things to Physna: it puts the file's bytes
there, and it PATCHes the VAMS metadata mirror -- which carries
``__VAMS__FileVersion``, the key every later staleness decision reads. When the
metadata half failed, the handler logged a warning, wrote a sync-tracking record
saying ``success``, and returned True. The SQS record was therefore deleted and never
redriven, and the one durable trace of the sync asserted an outcome that only half
happened. The sibling metadata-only path already recorded ``failed`` before acking.

Three branches of the upload flow reached that same false ``success``:

* the metadata PATCH raising ``PhysnaError`` after the bytes went up,
* no Physna asset UUID being obtainable, so the PATCH was never attempted,
* the "Physna already holds this S3 version" branch, whose metadata refresh failed
  (that one recorded nothing at all, so it acked with no evidence anywhere).

Every redrive assertion drives the real ``lambda_handler`` with an SQS -> SNS -> S3
payload, because ``batchItemFailures`` is what actually decides redrive and the event
source mapping is configured with ``reportBatchItemFailures``. Each failure case is
paired with a positive control that must still ack, so "everything fails now" cannot
pass.
"""

import json

import pytest
from unittest.mock import MagicMock

# Module-level import ensures the real `backend.backend.handlers` package is populated
# in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.addon.physna import physnaFileSync as _pfs  # noqa: F401

DB = "db-1"
ASSET = "asset-1"
RELATIVE = "/part.step"
BUCKET = "bucket-1"
S3_KEY = "prefix/asset-1/part.step"
MESSAGE_ID = "sqs-message-1"


def _sqs_s3_event(message_id=MESSAGE_ID):
    """The production payload shape: SQS record -> SNS notification -> S3 record."""
    s3_record = {
        "eventSource": "aws:s3",
        "eventName": "ObjectCreated:Put",
        "s3": {"bucket": {"name": BUCKET}, "object": {"key": S3_KEY}},
    }
    return {
        "Records": [
            {
                "eventSource": "aws:sqs",
                "messageId": message_id,
                "body": json.dumps(
                    {
                        "Type": "Notification",
                        "Message": json.dumps({"Records": [s3_record]}),
                    }
                ),
            }
        ]
    }


def _record_fields(records):
    """``(action, syncStatus, kwargs)`` for each sync-tracking record written."""
    out = []
    for args, kwargs in records:
        action = args[3] if len(args) > 3 else kwargs.get("action")
        status = args[4] if len(args) > 4 else kwargs.get("sync_status")
        out.append((action, status, kwargs))
    return out


def _recorded(records):
    """``{(action, syncStatus)}`` -- set containment, never call counts or order.

    An implementation that writes an extra record, or writes them in another order,
    is not worse and must not fail here.
    """
    return {(action, status) for action, status, _kwargs in _record_fields(records)}


def _statuses(records):
    return {status for _action, status in _recorded(records)}


@pytest.fixture
def harness(monkeypatch):
    """Wire the collaborators of the upload flow and capture what it records."""
    from backend.backend.handlers.addon.physna import physnaFileSync as pfs

    def _wire(
        *,
        existing_uuid=None,
        existing_file_version=None,
        uuid_after_upload=None,
        s3_version="v-1",
        upload_body=None,
        metadata_error=None,
    ):
        state = {"records": [], "downloads": [], "metadata_calls": []}

        monkeypatch.setattr(
            pfs,
            "_record_file_sync",
            lambda *args, **kwargs: state["records"].append((args, kwargs)),
        )
        monkeypatch.setattr(
            pfs,
            "_resolve_asset_from_s3_event",
            lambda bucket, key: {
                "databaseId": DB,
                "assetId": ASSET,
                "relativePath": RELATIVE,
                "bucketName": bucket,
                "s3Key": key,
                "assetDetails": {},
            },
        )
        monkeypatch.setattr(
            pfs,
            "_build_metadata_payload",
            lambda db, aid, rel, file_version=None, asset_details=None: {"user_key": "v"},
        )
        monkeypatch.setattr(pfs, "_get_s3_version_id", lambda bucket, key: s3_version)

        lookups = {"n": 0}

        def _lookup(_client, _tenant, _path):
            lookups["n"] += 1
            return existing_uuid if lookups["n"] == 1 else uuid_after_upload

        monkeypatch.setattr(pfs, "lookup_physna_asset_id", _lookup)
        monkeypatch.setattr(
            pfs,
            "get_physna_asset",
            lambda _c, _t, _u: (
                None
                if existing_uuid is None
                else {
                    "id": existing_uuid,
                    "metadata": (
                        {}
                        if existing_file_version is None
                        else {pfs.VAMS_RESERVED_FILE_VERSION_KEY: existing_file_version}
                    ),
                }
            ),
        )
        monkeypatch.setattr(pfs, "_delete_physna_asset_by_uuid", lambda *a, **k: None)

        def _download(_bucket, _key, local_path):
            state["downloads"].append(local_path)
            with open(local_path, "wb") as handle:
                handle.write(b"cad-bytes")

        monkeypatch.setattr(pfs._s3, "download_file", _download)

        def _update_metadata(_client, _full_path, uuid, payload):
            state["metadata_calls"].append((uuid, payload))
            if metadata_error is not None:
                # Raised as the module's own class so the handler's
                # ``except PhysnaError`` matches it whatever the import state.
                raise pfs.PhysnaError(metadata_error)
            return True

        monkeypatch.setattr(pfs, "_update_physna_metadata", _update_metadata)

        upload_response = MagicMock()
        upload_response.status = 201
        upload_response.data = json.dumps(
            {"id": "uuid-new"} if upload_body is None else upload_body
        ).encode("utf-8")
        client = MagicMock()
        client.request.return_value = upload_response
        monkeypatch.setattr(pfs, "PhysnaClient", lambda *a, **k: client)
        state["client"] = client
        return pfs, state

    return _wire


PATCH_FAILED = "Metadata update failed for db-1/asset-1/part.step with status 500"


@pytest.mark.unit
class TestMetadataPatchFailsAfterUpload:
    def test_the_sync_record_does_not_claim_success(self, harness):
        pfs, state = harness(metadata_error=PATCH_FAILED)

        ok = pfs._upload_file_to_physna(DB, ASSET, RELATIVE, BUCKET, S3_KEY)

        assert ok is False
        assert state["metadata_calls"], "the metadata half must have been attempted"
        assert (pfs.SYNC_ACTION_CREATE, pfs.SYNC_STATUS_FAILED) in _recorded(
            state["records"]
        ), f"expected a failed sync record, got {_recorded(state['records'])}"
        assert pfs.SYNC_STATUS_SUCCESS not in _statuses(state["records"]), (
            "a sync whose metadata half failed must not be recorded as a success"
        )

    def test_the_operator_gets_the_uuid_and_the_reason(self, harness):
        pfs, state = harness(metadata_error=PATCH_FAILED)

        pfs._upload_file_to_physna(DB, ASSET, RELATIVE, BUCKET, S3_KEY)

        failed = [
            kwargs
            for _action, status, kwargs in _record_fields(state["records"])
            if status == pfs.SYNC_STATUS_FAILED
        ]
        assert failed, "no failed record to inspect"
        assert failed[0].get("physna_asset_uuid") == "uuid-new"
        assert "metadata" in (failed[0].get("error_message") or "").lower(), (
            f"the recorded reason must name the half that failed; got "
            f"{failed[0].get('error_message')!r}"
        )

    def test_the_sqs_record_is_reported_for_redrive(self, harness):
        pfs, _state = harness(metadata_error=PATCH_FAILED)

        response = pfs.lambda_handler(_sqs_s3_event(), MagicMock())

        assert {"itemIdentifier": MESSAGE_ID} in response["batchItemFailures"], (
            "a record whose metadata half failed must be redriven, not deleted"
        )

    def test_a_fully_successful_sync_still_acks_and_records_success(self, harness):
        """Positive control: the batch must still drain when both halves land."""
        pfs, state = harness()

        response = pfs.lambda_handler(_sqs_s3_event(), MagicMock())

        assert response["batchItemFailures"] == []
        assert (pfs.SYNC_ACTION_CREATE, pfs.SYNC_STATUS_SUCCESS) in _recorded(
            state["records"]
        )
        assert pfs.SYNC_STATUS_FAILED not in _statuses(state["records"])


@pytest.mark.unit
class TestNoAssetUuidObtainable:
    def test_a_metadata_set_that_was_never_attempted_is_not_a_success(self, harness):
        pfs, state = harness(upload_body={"noIdHere": True}, uuid_after_upload=None)

        ok = pfs._upload_file_to_physna(DB, ASSET, RELATIVE, BUCKET, S3_KEY)

        assert ok is False
        assert state["metadata_calls"] == [], (
            "this branch exists precisely because the PATCH cannot be issued"
        )
        assert pfs.SYNC_STATUS_FAILED in _statuses(state["records"])
        assert pfs.SYNC_STATUS_SUCCESS not in _statuses(state["records"])

    def test_the_sqs_record_is_reported_for_redrive(self, harness):
        pfs, _state = harness(upload_body={"noIdHere": True}, uuid_after_upload=None)

        response = pfs.lambda_handler(_sqs_s3_event(), MagicMock())

        assert {"itemIdentifier": MESSAGE_ID} in response["batchItemFailures"]

    def test_a_uuid_recovered_after_the_upload_still_acks(self, harness):
        """Positive control: the fallback lookup succeeding is a complete sync."""
        pfs, state = harness(
            upload_body={"noIdHere": True}, uuid_after_upload="uuid-late"
        )

        response = pfs.lambda_handler(_sqs_s3_event(), MagicMock())

        assert response["batchItemFailures"] == []
        assert [uuid for uuid, _payload in state["metadata_calls"]] == ["uuid-late"]
        assert pfs.SYNC_STATUS_FAILED not in _statuses(state["records"])


@pytest.mark.unit
class TestMetadataRefreshOnAnUpToDateCopy:
    """The branch where Physna already holds the current S3 version."""

    def test_a_failed_refresh_is_recorded_and_redriven(self, harness):
        pfs, state = harness(
            existing_uuid="uuid-1",
            existing_file_version="v-1",
            s3_version="v-1",
            metadata_error="Metadata update failed with status 503",
        )

        response = pfs.lambda_handler(_sqs_s3_event(), MagicMock())

        assert state["downloads"] == [], (
            "the up-to-date branch must not re-upload; this test would otherwise "
            "be exercising the fresh-upload path instead"
        )
        assert (pfs.SYNC_ACTION_MODIFY, pfs.SYNC_STATUS_FAILED) in _recorded(
            state["records"]
        )
        assert pfs.SYNC_STATUS_SUCCESS not in _statuses(state["records"])
        assert {"itemIdentifier": MESSAGE_ID} in response["batchItemFailures"]

    def test_a_successful_refresh_still_acks(self, harness):
        """Positive control for the branch above."""
        pfs, state = harness(
            existing_uuid="uuid-1", existing_file_version="v-1", s3_version="v-1"
        )

        response = pfs.lambda_handler(_sqs_s3_event(), MagicMock())

        assert response["batchItemFailures"] == []
        assert state["downloads"] == []
        assert pfs.SYNC_STATUS_FAILED not in _statuses(state["records"])

    def test_the_refresh_carries_the_version_key_forward(self, harness):
        """The refresh must SEND ``__VAMS__FileVersion``, not merely leave it alone.

        ``_update_physna_metadata`` is a full replace: it computes the keys absent from the
        payload and DELETEs them from Physna. So a refresh that omits the version key removes
        it, and every later staleness decision then reads a file with no version -- which routes
        the next sync into the delete-and-re-upload path. Omitting the key is therefore the
        opposite of preserving it, and the redrive this branch performs makes it worse: each
        redelivery would strip the tag again.

        Asserted on the payload handed to ``_update_physna_metadata`` rather than on a Physna
        call count, because the value is the whole point -- an empty or wrong version is as
        damaging as an absent one.
        """
        pfs, state = harness(
            existing_uuid="uuid-1", existing_file_version="v-1", s3_version="v-1"
        )

        pfs.lambda_handler(_sqs_s3_event(), MagicMock())

        assert state["metadata_calls"], (
            "the refresh must have been attempted, or the assertion below is vacuous"
        )
        _uuid, payload = state["metadata_calls"][-1]
        assert payload.get(pfs.VAMS_RESERVED_FILE_VERSION_KEY) == "v-1", (
            "the up-to-date branch sent "
            f"{payload.get(pfs.VAMS_RESERVED_FILE_VERSION_KEY)!r} for the version key; a full "
            "replace deletes any key it does not carry"
        )
