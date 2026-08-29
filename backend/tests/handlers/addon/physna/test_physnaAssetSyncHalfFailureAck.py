# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An asset-level Physna sync must not ack a file whose sync only half happened.

``physnaFileSync._upload_file_to_physna`` answers False when the bytes reached Physna but
the metadata PATCH that writes ``__VAMS__FileVersion`` did not. On the file-sync queue that
answer is what reports the SQS record for redrive (``test_physnaUploadHalfFailureAck.py``).
The asset-sync handler reaches the same upload from two places -- the stale-copy re-upload
inside the per-file loop, and the repair upload for files Physna appears not to hold -- so
the same answer has to travel the same way. When both call sites discarded it, the redrive
guarantee held only for messages arriving on the file-sync queue: the identical
half-completed upload driven from the asset-sync queue was acked, SQS deleted the message,
and the sync-tracking record for the asset asserted ``success``.

An exception from the upload is the same outcome for that file and is reported the same way,
matching ``physnaFileSync``'s per-record isolation: the asset's remaining files are still
attempted, and the SQS record is reported once at the end.

Every redrive assertion drives the real ``lambda_handler`` with an SQS -> SNS -> DynamoDB
stream payload, because ``batchItemFailures`` is what decides redrive and the event source
mapping is configured with ``reportBatchItemFailures``. Each failure case is paired with a
positive control that must still ack, so "everything fails now" cannot pass.
"""

import json

import pytest
from unittest.mock import MagicMock

# Module-level import ensures the real `backend.backend.handlers` package is populated
# in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.addon.physna import physnaAssetSync as _pas  # noqa: F401

DB = "db-1"
ASSET = "asset-1"
MESSAGE_ID = "sqs-asset-message-1"
BUCKET = "bucket-1"
ASSET_BASE_KEY = "prefix/asset-1/"


def _asset_sqs_event(event_name="MODIFY", message_id=MESSAGE_ID):
    """The production payload shape: SQS record -> SNS notification -> DynamoDB stream."""
    stream_record = {
        "eventSource": "aws:dynamodb",
        "eventName": event_name,
        "dynamodb": {
            "Keys": {"databaseId": {"S": DB}, "assetId": {"S": ASSET}},
            "NewImage": {"databaseId": {"S": DB}, "assetId": {"S": ASSET}},
        },
    }
    return {
        "Records": [
            {
                "eventSource": "aws:sqs",
                "messageId": message_id,
                "body": json.dumps(
                    {
                        "Type": "Notification",
                        "Message": json.dumps(stream_record),
                    }
                ),
            }
        ]
    }


def _recorded(records):
    """``{(action, syncStatus)}`` -- set containment, never call counts or order.

    ``_record_asset_sync(database_id, asset_id, action, sync_status, error_message=None)``,
    so the action and status are the third and fourth positional arguments; either may
    instead arrive by keyword. An implementation that writes an extra tracking record, or
    writes them in another order, is not worse and must not fail here.
    """
    out = set()
    for args, kwargs in records:
        action = args[2] if len(args) > 2 else kwargs.get("action")
        status = args[3] if len(args) > 3 else kwargs.get("sync_status")
        out.add((action, status))
    return out


def _statuses(records):
    return {status for _action, status in _recorded(records)}


@pytest.fixture
def harness(monkeypatch):
    """Wire the collaborators of the asset re-sync and capture what it records."""
    from backend.backend.handlers.addon.physna import physnaAssetSync as pas

    def _wire(
        *,
        physna_items=(),
        vams_paths=(),
        upload_results=None,
        archived=False,
    ):
        """``upload_results`` maps an asset-relative path to what the upload does:
        True/False for the value ``_upload_file_to_physna`` answers, or an Exception
        instance to raise. A path it does not name uploads cleanly."""
        state = {"records": [], "uploads": []}
        results = dict(upload_results or {})

        monkeypatch.setattr(
            pas,
            "_record_asset_sync",
            lambda *args, **kwargs: state["records"].append((args, kwargs)),
        )
        monkeypatch.setattr(
            pas,
            "get_asset_details",
            lambda database_id, asset_id: (
                None
                if archived and "#deleted" not in database_id
                else {
                    "assetName": "My Asset",
                    "bucketId": "b-1",
                    "assetLocation": {"Key": ASSET_BASE_KEY},
                }
            ),
        )
        monkeypatch.setattr(
            pas,
            "get_bucket_details",
            lambda _bucket_id: {
                "bucketName": BUCKET,
                "baseAssetsPrefix": "prefix/",
            },
        )
        monkeypatch.setattr(pas, "get_asset_metadata", lambda _db, _asset: {})
        monkeypatch.setattr(pas, "get_file_metadata", lambda _db, _asset, _rel: ({}, {}))
        monkeypatch.setattr(
            pas, "list_physna_assets_under", lambda *a, **k: iter(list(physna_items))
        )
        monkeypatch.setattr(
            pas, "_list_vams_file_paths", lambda _db, _asset: set(vams_paths)
        )
        monkeypatch.setattr(pas, "ensure_metadata_fields_registered", lambda *a, **k: None)
        monkeypatch.setattr(pas, "delete_physna_metadata_fields", lambda *a, **k: None)
        monkeypatch.setattr(pas, "delete_folder_if_empty", lambda *a, **k: None)
        monkeypatch.setattr(pas, "_prefetch_file_metadata", lambda _db, _asset: {})

        def _upload(database_id, asset_id, relative, bucket_name, s3_key, client=None):
            state["uploads"].append((relative, bucket_name, s3_key))
            outcome = results.get(relative, True)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        monkeypatch.setattr(pas.physnaFileSync, "_upload_file_to_physna", _upload)

        response = MagicMock()
        response.status = 204
        client = MagicMock()
        client.request.return_value = response
        monkeypatch.setattr(pas, "PhysnaClient", lambda *a, **k: client)
        state["client"] = client
        return pas, state

    return _wire


def _stale_listing_item(relative="/part.step"):
    """A Physna copy of a VAMS file carrying no ``__VAMS__FileVersion``: the state the
    per-file loop treats as stale and routes to the re-upload."""
    return {
        "id": "uuid-1",
        "path": f"{DB}/{ASSET}{relative}",
        "metadata": {},
    }


@pytest.mark.unit
class TestStaleCopyReuploadHalfFails:
    """The re-upload inside the per-file loop (the missing-version-tag branch)."""

    def test_the_record_is_reported_for_redrive(self, harness):
        pas, state = harness(
            physna_items=[_stale_listing_item()],
            vams_paths={"/part.step"},
            upload_results={"/part.step": False},
        )

        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert state["uploads"], "the re-upload must have been attempted"
        assert {"itemIdentifier": MESSAGE_ID} in response["batchItemFailures"], (
            "a half-completed upload driven from the asset-sync queue must be redriven, "
            "not deleted"
        )

    def test_the_sync_record_does_not_claim_success(self, harness):
        pas, state = harness(
            physna_items=[_stale_listing_item()],
            vams_paths={"/part.step"},
            upload_results={"/part.step": False},
        )

        pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert (pas.SYNC_ACTION_MODIFY, pas.SYNC_STATUS_FAILED) in _recorded(
            state["records"]
        ), f"expected a failed asset record, got {_recorded(state['records'])}"
        assert pas.SYNC_STATUS_SUCCESS not in _statuses(state["records"]), (
            "an asset sync that left a file half-synced must not be recorded as a success"
        )

    def test_an_exception_from_the_upload_is_reported_too(self, harness):
        pas, state = harness(
            physna_items=[_stale_listing_item()],
            vams_paths={"/part.step"},
            upload_results={"/part.step": RuntimeError("Physna upload rejected")},
        )

        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert {"itemIdentifier": MESSAGE_ID} in response["batchItemFailures"]
        assert pas.SYNC_STATUS_FAILED in _statuses(state["records"])
        assert pas.SYNC_STATUS_SUCCESS not in _statuses(state["records"])

    def test_a_completed_reupload_still_acks(self, harness):
        """Positive control: the batch must still drain when both halves land."""
        pas, state = harness(
            physna_items=[_stale_listing_item()],
            vams_paths={"/part.step"},
        )

        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert state["uploads"], "the control must exercise the same upload path"
        assert response["batchItemFailures"] == []
        assert (pas.SYNC_ACTION_MODIFY, pas.SYNC_STATUS_SUCCESS) in _recorded(
            state["records"]
        )
        assert pas.SYNC_STATUS_FAILED not in _statuses(state["records"])


@pytest.mark.unit
class TestRepairUploadHalfFails:
    """The repair upload for a VAMS file the narrowed Physna listing did not return."""

    def test_the_record_is_reported_for_redrive(self, harness):
        pas, state = harness(
            physna_items=[],
            vams_paths={"/housing/pump.stl"},
            upload_results={"/housing/pump.stl": False},
        )

        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        # Containment, not the exact upload sequence: a repair that also re-uploaded a sibling,
        # or retried this one, is not worse and must not fail here. Membership cannot hold on an
        # empty set, so the assertions below are still not left vacuous.
        assert "/housing/pump.stl" in {rel for rel, _b, _k in state["uploads"]}, (
            f"the repair upload was never attempted, so the assertions below would be vacuous; "
            f"attempted {state['uploads']}"
        )
        assert {"itemIdentifier": MESSAGE_ID} in response["batchItemFailures"]
        assert pas.SYNC_STATUS_SUCCESS not in _statuses(state["records"])

    def test_the_s3_key_is_the_file_s_own_key_under_the_asset_root(self, harness):
        """The repair upload's own contract, so the redrive assertions above are not
        proving a failure that came from a malformed key."""
        pas, state = harness(physna_items=[], vams_paths={"/housing/pump.stl"})

        pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert (BUCKET, ASSET_BASE_KEY + "housing/pump.stl") in {
            (bucket, key) for _rel, bucket, key in state["uploads"]
        }

    def test_one_file_s_shortfall_does_not_abandon_the_others(self, harness):
        """Every eligible file is still attempted, and the record is still reported."""
        pas, state = harness(
            physna_items=[],
            vams_paths={"/a.stl", "/b.stl", "/c.stl"},
            upload_results={"/b.stl": RuntimeError("Physna upload rejected")},
        )

        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert {"/a.stl", "/b.stl", "/c.stl"} <= {
            rel for rel, _b, _k in state["uploads"]
        }, f"a failure stopped the remaining files; attempted {state['uploads']}"
        assert {"itemIdentifier": MESSAGE_ID} in response["batchItemFailures"]

    def test_a_completed_repair_upload_still_acks(self, harness):
        """Positive control for the branch above."""
        pas, state = harness(physna_items=[], vams_paths={"/housing/pump.stl"})

        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert state["uploads"], "the control must exercise the same upload path"
        assert response["batchItemFailures"] == []
        assert pas.SYNC_STATUS_FAILED not in _statuses(state["records"])


@pytest.mark.unit
class TestPathsThatAttemptNoUpload:
    """Paths that upload nothing must keep acking -- the fix must not widen to them."""

    def test_a_permanent_delete_acks(self, harness):
        """The delete branch returns before any upload, so it has nothing to redrive."""
        pas, state = harness(archived=True, physna_items=[], vams_paths=set())
        # An asset row absent under BOTH the live and the '#deleted' key is a permanent
        # delete; the harness's archived row answers only for the '#deleted' key, so make
        # both absent for this case.
        import backend.backend.handlers.addon.physna.physnaAssetSync as module

        original = module.get_asset_details
        module.get_asset_details = lambda _db, _asset: None
        try:
            response = pas.lambda_handler(
                _asset_sqs_event(event_name="REMOVE"), MagicMock()
            )
        finally:
            module.get_asset_details = original

        assert response["batchItemFailures"] == []
        assert state["uploads"] == [], "a delete must attempt no upload"
        assert (pas.SYNC_ACTION_DELETE, pas.SYNC_STATUS_SUCCESS) in _recorded(
            state["records"]
        )

    def test_an_archived_asset_acks_without_uploading(self, harness):
        """An archived asset's objects are delete-marked, so there are no bytes to read
        and the Physna copies are what the unarchive relies on."""
        pas, state = harness(
            archived=True, physna_items=[], vams_paths={"/housing/pump.stl"}
        )

        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert state["uploads"] == []
        assert response["batchItemFailures"] == []
        assert pas.SYNC_STATUS_FAILED not in _statuses(state["records"])


def _permanent_delete(pas, harness_state_pair, event_name="REMOVE"):
    """Drive the permanent-delete route: the asset row is absent under BOTH keys."""
    import backend.backend.handlers.addon.physna.physnaAssetSync as module

    original = module.get_asset_details
    module.get_asset_details = lambda _db, _asset: None
    try:
        return pas.lambda_handler(_asset_sqs_event(event_name=event_name), MagicMock())
    finally:
        module.get_asset_details = original


@pytest.mark.unit
class TestDeleteRouteUnaddressableCopy:
    """A listing item with no addressable UUID is a shortfall on the delete route too.

    `_delete_by_item` keys Physna's asset-scoped DELETE on the listing item's UUID. An item that
    carries none cannot be deleted at all — the identical condition the metadata route reports by
    setting `sync_complete = False`. On the delete route the VAMS asset is gone under both the live
    and the archived key, so no later event ever names it again: acking leaves that copy in the
    tenant permanently, which is strictly worse than the metadata-route case it was inconsistent
    with.
    """

    def test_an_item_with_no_uuid_is_reported_rather_than_acked(self, harness):
        pas, state = harness(
            physna_items=[{"path": "db-1/asset-1/part.step"}],   # no id/assetId/uuid
            vams_paths=set(),
        )

        response = _permanent_delete(pas, state)

        assert response["batchItemFailures"], (
            "the Physna copy could not be addressed, so it is still in the tenant, and the VAMS "
            "asset is gone under both keys — no later event will name it"
        )

    def test_no_delete_was_issued_for_it(self, harness):
        """Distinguishes 'could not address it' from 'deleted it and failed to notice'."""
        pas, state = harness(
            physna_items=[{"path": "db-1/asset-1/part.step"}],
            vams_paths=set(),
        )

        _permanent_delete(pas, state)

        deletes = [
            call for call in state["client"].request.call_args_list
            if call.args and call.args[0] == "DELETE"
        ]
        assert deletes == [], f"no DELETE is addressable without a UUID; got {deletes}"

    def test_an_addressable_item_still_acks(self, harness):
        """Positive control: the ordinary delete keeps acking, so the fix has not widened."""
        pas, state = harness(
            physna_items=[{"path": "db-1/asset-1/part.step", "id": "uuid-1"}],
            vams_paths=set(),
        )

        response = _permanent_delete(pas, state)

        assert response["batchItemFailures"] == []
        assert (pas.SYNC_ACTION_DELETE, pas.SYNC_STATUS_SUCCESS) in _recorded(
            state["records"]
        )

    def test_one_unaddressable_item_does_not_stop_the_others(self, harness):
        """The loop still attempts every copy: a shortfall reports, it does not abandon."""
        pas, state = harness(
            physna_items=[
                {"path": "db-1/asset-1/a.step"},                      # unaddressable
                {"path": "db-1/asset-1/b.step", "id": "uuid-b"},      # addressable
            ],
            vams_paths=set(),
        )

        response = _permanent_delete(pas, state)

        # Set containment over the UUIDs actually deleted, rather than a call count: an
        # implementation that retried or batched the addressable delete is not worse, and pinning
        # the count would reject it.
        deleted_uuids = {
            call.args[1].rsplit("/", 1)[-1]
            for call in state["client"].request.call_args_list
            if call.args and call.args[0] == "DELETE"
        }
        assert "uuid-b" in deleted_uuids, (
            f"the addressable copy must still be deleted; deleted {deleted_uuids}")
        assert response["batchItemFailures"], "the unaddressable one must still report"
