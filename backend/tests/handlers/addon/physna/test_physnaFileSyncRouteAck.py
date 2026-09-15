# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""No ``physnaFileSync`` route may ack an SQS record whose Physna write did not land.

Guards FIX-015 (S2-BACKEND-024) on the file-sync side. The asset-sync handler already
turns every shortfall into a reported ``batchItemFailures`` entry; the routes covered here
each acked instead, so SQS deleted the message and the divergence became permanent with
nothing but a log line recording it.

Four routes, each with its own failure mode:

* the **metadata-only route** an asset-or-file metadata edit takes, whose PATCH is the
  whole sync -- it uploads nothing, so a failed PATCH means Physna keeps values VAMS no
  longer has or never receives the ones it does;
* the **stale-key prune** inside ``_update_physna_metadata``, whose failure had no signal
  of any kind: no raise, no tracking row, no redrive, and a docstring that blessed it;
* the **reconcile-delete** of a permanently deleted VAMS file, whose failure left an
  orphaned Physna copy that no later event names again (every S3 version is purged);
* the **stale copy Physna would not release** -- when the delete-before-re-upload fails
  and the upload is then rejected as already existing, Physna still holds the OLD bytes,
  so writing the current ``__VAMS__FileVersion`` onto them labels stale geometry as
  current and no later staleness check ever looks at it again.

Each failure assertion is paired with a positive control that must still ack, so a handler
that simply reports everything cannot pass. Redrive is asserted through the real
``lambda_handler`` with the production SQS -> SNS -> (S3 record | stream record) envelope,
because ``batchItemFailures`` is what actually decides redrive; a redrive is bounded by
the queue's ``maxReceiveCount`` of three and then its own dead-letter queue.
"""

import json

import pytest
from unittest.mock import MagicMock

# Module-level import ensures the real `backend.backend.handlers` package is populated in
# sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.addon.physna import physnaFileSync as _pfs  # noqa: F401

DB = "db-1"
ASSET = "asset-1"
RELATIVE = "/part.step"
COMPOSITE = f"{DB}:{ASSET}:{RELATIVE}"
BUCKET = "bucket-1"
S3_KEY = "prefix/asset-1/part.step"
MESSAGE_ID = "sqs-message-1"


def _sqs_envelope(sns_message, message_id=MESSAGE_ID):
    return {
        "Records": [
            {
                "eventSource": "aws:sqs",
                "messageId": message_id,
                "body": json.dumps(
                    {"Type": "Notification", "Message": json.dumps(sns_message)}
                ),
            }
        ]
    }


def _stream_record(event_name="MODIFY"):
    """A file-metadata DynamoDB stream record, as the SNS message carries it."""
    return {
        "eventSource": "aws:dynamodb",
        "eventName": event_name,
        "dynamodb": {
            "Keys": {"databaseId:assetId:filePath": {"S": COMPOSITE}},
            "NewImage": {"databaseId:assetId:filePath": {"S": COMPOSITE}},
        },
    }


def _stream_event(message_id=MESSAGE_ID, event_name="MODIFY"):
    """SQS -> SNS -> file-metadata DynamoDB stream record."""
    return _sqs_envelope(_stream_record(event_name), message_id=message_id)


def _s3_event(message_id=MESSAGE_ID):
    """SQS -> SNS -> S3 ObjectCreated record."""
    return _sqs_envelope(
        {
            "Records": [
                {
                    "eventSource": "aws:s3",
                    "eventName": "ObjectCreated:Put",
                    "s3": {"bucket": {"name": BUCKET}, "object": {"key": S3_KEY}},
                }
            ]
        },
        message_id=message_id,
    )


def _record_fields(records):
    """``(action, syncStatus, kwargs)`` for each sync-tracking record written."""
    out = []
    for args, kwargs in records:
        action = args[3] if len(args) > 3 else kwargs.get("action")
        status = args[4] if len(args) > 4 else kwargs.get("sync_status")
        out.append((action, status, kwargs))
    return out


def _recorded(records):
    """``{(action, syncStatus)}`` -- set containment, never counts or order.

    An implementation that writes an extra record, or writes them in another order, is not
    worse and must not fail here.
    """
    return {(action, status) for action, status, _kwargs in _record_fields(records)}


def _statuses(records):
    return {status for _action, status in _recorded(records)}


def _identifiers(response):
    """The reported identifiers, with the entry shape checked.

    A malformed entry makes Lambda fail the ENTIRE batch, so the key name and a non-empty
    value are part of the contract.
    """
    assert "batchItemFailures" in response, (
        "no batchItemFailures field: the event source mapping reads that as a whole-batch "
        "success and deletes every message in it"
    )
    for entry in response["batchItemFailures"]:
        assert list(entry) == ["itemIdentifier"], f"unexpected entry shape: {entry}"
        assert entry["itemIdentifier"], "an empty identifier fails the whole batch"
    return [entry["itemIdentifier"] for entry in response["batchItemFailures"]]


def _no_such_key():
    from botocore.exceptions import ClientError

    return ClientError(
        error_response={
            "Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        },
        operation_name="GetObject",
    )


# ---------------------------------------------------------------------------
# The metadata-only route: it uploads nothing, so the PATCH is the whole sync.
# ---------------------------------------------------------------------------


@pytest.fixture
def metadata_route(monkeypatch):
    from backend.backend.handlers.addon.physna import physnaFileSync as pfs

    def _wire(*, existing_version="v-1", metadata_error=None):
        state = {"records": [], "metadata_calls": [], "uploads": []}

        monkeypatch.setattr(
            pfs,
            "_record_file_sync",
            lambda *args, **kwargs: state["records"].append((args, kwargs)),
        )
        monkeypatch.setattr(
            pfs,
            "get_asset_details",
            lambda db, aid: {
                "assetName": "My Asset",
                "bucketId": "b-1",
                "assetLocation": {"Key": "prefix/asset-1/"},
            },
        )
        monkeypatch.setattr(
            pfs,
            "get_bucket_details",
            lambda bid: {"bucketName": BUCKET, "baseAssetsPrefix": "prefix/"},
        )
        monkeypatch.setattr(pfs, "lookup_physna_asset_id", lambda *a: "uuid-1")
        monkeypatch.setattr(
            pfs,
            "get_physna_asset",
            lambda _c, _t, _u: {
                "id": "uuid-1",
                "metadata": (
                    {}
                    if existing_version is None
                    else {pfs.VAMS_RESERVED_FILE_VERSION_KEY: existing_version}
                ),
            },
        )
        monkeypatch.setattr(
            pfs,
            "_build_metadata_payload",
            lambda db, aid, rel, file_version=None, asset_details=None: {"user_key": "v"},
        )

        def _update_metadata(_client, _full_path, uuid, payload):
            state["metadata_calls"].append((uuid, payload))
            if metadata_error is not None:
                raise pfs.PhysnaError(metadata_error)
            return True

        monkeypatch.setattr(pfs, "_update_physna_metadata", _update_metadata)

        # Reaching the upload path would mean this test is not exercising the
        # metadata-only route at all, so record it rather than letting it run.
        monkeypatch.setattr(
            pfs,
            "_upload_file_to_physna",
            lambda *a, **k: state["uploads"].append(a) or True,
        )
        monkeypatch.setattr(pfs, "PhysnaClient", lambda *a, **k: MagicMock())
        return pfs, state

    return _wire


PATCH_FAILED = "Metadata update failed with status 500"


@pytest.mark.unit
class TestMetadataOnlyRoutePatchFailure:
    def test_a_failed_patch_is_not_reported_as_handled(self, metadata_route):
        pfs, state = metadata_route(metadata_error=PATCH_FAILED)

        ok = pfs._handle_file_metadata_stream(_stream_record())

        assert state["uploads"] == [], (
            "the metadata-only route must not have fallen through to an upload, or this "
            "test is exercising a different route"
        )
        assert state["metadata_calls"], "the PATCH must have been attempted"
        assert ok is False, (
            "a route whose only Physna write failed answered handled, which acks the "
            "SQS record and deletes it"
        )
        assert (pfs.SYNC_ACTION_MODIFY, pfs.SYNC_STATUS_FAILED) in _recorded(
            state["records"]
        ), f"expected a failed sync record, got {_recorded(state['records'])}"
        assert pfs.SYNC_STATUS_SUCCESS not in _statuses(state["records"])

    def test_the_sqs_record_is_reported_for_redrive(self, metadata_route):
        pfs, _state = metadata_route(metadata_error=PATCH_FAILED)

        response = pfs.lambda_handler(_stream_event(), MagicMock())

        assert _identifiers(response) == [MESSAGE_ID], (
            "a metadata edit whose PATCH failed must be redriven, not deleted"
        )

    def test_a_successful_patch_still_acks_and_records_success(self, metadata_route):
        """Positive control: the batch must still drain when the PATCH lands."""
        pfs, state = metadata_route()

        response = pfs.lambda_handler(_stream_event(), MagicMock())

        assert _identifiers(response) == []
        assert (pfs.SYNC_ACTION_MODIFY, pfs.SYNC_STATUS_SUCCESS) in _recorded(
            state["records"]
        )
        assert pfs.SYNC_STATUS_FAILED not in _statuses(state["records"])

    def test_the_redrive_cannot_escalate_into_a_re_upload(self, metadata_route):
        """The payload must carry ``__VAMS__FileVersion`` forward.

        ``_update_physna_metadata`` is a full replace: it DELETEs every Physna key the
        payload does not carry. A payload that omits the version key therefore strips it,
        and a copy with no version tag is what this module treats as stale -- so each
        redelivery of a redriven record would escalate a metadata edit into a
        delete-and-re-upload. Asserted on the value, because an empty or wrong version is
        as damaging as an absent one.
        """
        pfs, state = metadata_route(metadata_error=PATCH_FAILED)

        pfs.lambda_handler(_stream_event(), MagicMock())

        assert state["metadata_calls"], "no PATCH payload to inspect"
        _uuid, payload = state["metadata_calls"][-1]
        assert payload.get(pfs.VAMS_RESERVED_FILE_VERSION_KEY) == "v-1", (
            f"the route sent {payload.get(pfs.VAMS_RESERVED_FILE_VERSION_KEY)!r} for the "
            f"version key; a full replace deletes any key it does not carry"
        )


# ---------------------------------------------------------------------------
# The stale-key prune inside _update_physna_metadata.
# ---------------------------------------------------------------------------


@pytest.fixture
def prune(monkeypatch):
    """Drive ``_update_physna_metadata`` with a scripted prune and PATCH outcome."""
    from backend.backend.handlers.addon.physna import physnaFileSync as pfs

    def _wire(*, physna_metadata, prune_error=None, patch_status=200):
        state = {"prunes": [], "requests": []}

        monkeypatch.setattr(
            pfs, "ensure_metadata_fields_registered", lambda *a, **k: None
        )
        monkeypatch.setattr(
            pfs,
            "get_physna_asset",
            lambda _c, _t, _u: {"id": "uuid-1", "metadata": dict(physna_metadata)},
        )

        def _delete_fields(_client, _tenant, _uuid, names):
            state["prunes"].append(sorted(names))
            if prune_error is not None:
                raise pfs.PhysnaError(prune_error)

        monkeypatch.setattr(pfs, "delete_physna_metadata_fields", _delete_fields)

        response = MagicMock()
        response.status = patch_status
        response.data = b"{}"
        client = MagicMock()

        def _request(method, path, **kwargs):
            state["requests"].append((method, path, kwargs))
            return response

        client.request.side_effect = _request
        state["client"] = client
        return pfs, state

    return _wire


def _patch_bodies(state):
    return [
        json.loads(kwargs["body"].decode("utf-8"))
        for method, _path, kwargs in state["requests"]
        if method == "PATCH"
    ]


PRUNE_FAILED = "metadata field DELETE returned 500"


@pytest.mark.unit
class TestStaleKeyPruneFailure:
    def test_a_failed_prune_is_raised_so_the_caller_can_report_it(self, prune):
        pfs, state = prune(physna_metadata={"gone": "x"}, prune_error=PRUNE_FAILED)

        with pytest.raises(pfs.PhysnaError) as excinfo:
            pfs._update_physna_metadata(
                state["client"], "db-1/asset-1/part.step", "uuid-1", {"kept": "y"}
            )

        assert ["gone"] in state["prunes"], (
            "the prune must have been attempted, or the raise proves nothing"
        )
        assert "prune" in str(excinfo.value).lower(), (
            f"the raised reason must name the half that failed; got {excinfo.value!r}"
        )

    def test_the_patch_still_goes_out_before_the_raise(self, prune):
        """The values VAMS DOES have must still land.

        Raising before the PATCH would leave both halves undone, which is worse than the
        swallow it replaces. This is the control that the raise is a report and not an
        early exit.
        """
        pfs, state = prune(physna_metadata={"gone": "x"}, prune_error=PRUNE_FAILED)

        with pytest.raises(pfs.PhysnaError):
            pfs._update_physna_metadata(
                state["client"], "db-1/asset-1/part.step", "uuid-1", {"kept": "y"}
            )

        assert {"metadata": {"kept": "y"}} in _patch_bodies(state), (
            f"the PATCH did not carry the desired payload; requests were "
            f"{[m for m, _p, _k in state['requests']]}"
        )

    def test_a_successful_prune_does_not_raise(self, prune):
        """Positive control: the ordinary full-replace path is unchanged."""
        pfs, state = prune(physna_metadata={"gone": "x"})

        ok = pfs._update_physna_metadata(
            state["client"], "db-1/asset-1/part.step", "uuid-1", {"kept": "y"}
        )

        assert ok is True
        assert ["gone"] in state["prunes"]
        assert {"metadata": {"kept": "y"}} in _patch_bodies(state)

    def test_a_404_on_the_patch_outranks_the_prune_failure(self, prune):
        """The asset is gone from Physna, so the keys that resisted pruning went with it.

        Answering False here (rather than raising) is what routes the caller to its upload
        fallback, which re-establishes both halves.
        """
        pfs, state = prune(
            physna_metadata={"gone": "x"}, prune_error=PRUNE_FAILED, patch_status=404
        )

        ok = pfs._update_physna_metadata(
            state["client"], "db-1/asset-1/part.step", "uuid-1", {"kept": "y"}
        )

        assert ok is False

    def test_a_failed_prune_with_nothing_left_to_patch_still_raises(self, prune):
        """An empty payload skips the PATCH, so the prune failure is the only outcome."""
        pfs, state = prune(physna_metadata={"gone": "x"}, prune_error=PRUNE_FAILED)

        with pytest.raises(pfs.PhysnaError):
            pfs._update_physna_metadata(
                state["client"], "db-1/asset-1/part.step", "uuid-1", {}
            )

        assert ["gone"] in state["prunes"]
        assert _patch_bodies(state) == [], "an empty payload has nothing to PATCH"


@pytest.mark.unit
class TestPruneFailureReachesTheQueue:
    """The prune failure has to travel: raised, recorded, and reported for redrive."""

    def test_a_failed_prune_on_the_metadata_route_redrives_the_record(
        self, monkeypatch
    ):
        from backend.backend.handlers.addon.physna import physnaFileSync as pfs

        records = []
        monkeypatch.setattr(
            pfs, "_record_file_sync", lambda *a, **k: records.append((a, k))
        )
        monkeypatch.setattr(
            pfs,
            "get_asset_details",
            lambda db, aid: {
                "assetName": "My Asset",
                "bucketId": "b-1",
                "assetLocation": {"Key": "prefix/asset-1/"},
            },
        )
        monkeypatch.setattr(
            pfs,
            "get_bucket_details",
            lambda bid: {"bucketName": BUCKET, "baseAssetsPrefix": "prefix/"},
        )
        monkeypatch.setattr(pfs, "lookup_physna_asset_id", lambda *a: "uuid-1")
        monkeypatch.setattr(
            pfs,
            "get_physna_asset",
            lambda _c, _t, _u: {
                "id": "uuid-1",
                "metadata": {
                    pfs.VAMS_RESERVED_FILE_VERSION_KEY: "v-1",
                    "gone": "x",
                },
            },
        )
        monkeypatch.setattr(
            pfs,
            "_build_metadata_payload",
            lambda db, aid, rel, file_version=None, asset_details=None: {"kept": "y"},
        )
        monkeypatch.setattr(
            pfs, "ensure_metadata_fields_registered", lambda *a, **k: None
        )

        prunes = []

        def _delete_fields(_client, _tenant, _uuid, names):
            prunes.append(sorted(names))
            raise pfs.PhysnaError(PRUNE_FAILED)

        monkeypatch.setattr(pfs, "delete_physna_metadata_fields", _delete_fields)

        ok_response = MagicMock()
        ok_response.status = 200
        ok_response.data = b"{}"
        client = MagicMock()
        client.request.return_value = ok_response
        monkeypatch.setattr(pfs, "PhysnaClient", lambda *a, **k: client)

        response = pfs.lambda_handler(_stream_event(), MagicMock())

        assert ["gone"] in prunes, (
            "the prune must have been attempted, or nothing below is about a prune"
        )
        assert _identifiers(response) == [MESSAGE_ID]
        assert pfs.SYNC_STATUS_FAILED in _statuses(records)
        assert pfs.SYNC_STATUS_SUCCESS not in _statuses(records)


# ---------------------------------------------------------------------------
# The reconcile-delete of a permanently deleted VAMS file.
# ---------------------------------------------------------------------------


@pytest.fixture
def reconcile(monkeypatch):
    """A permanently deleted S3 object whose Physna copy is still there."""
    from backend.backend.handlers.addon.physna import physnaFileSync as pfs

    def _wire(*, delete_error=None):
        state = {"records": [], "deletes": []}

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
        # No resolvable S3 version: the object is gone, not merely named under
        # another spelling of the key.
        monkeypatch.setattr(pfs, "_get_s3_version_id", lambda bucket, key: None)
        monkeypatch.setattr(pfs, "lookup_physna_asset_id", lambda *a: "uuid-stale")
        monkeypatch.setattr(
            pfs,
            "get_physna_asset",
            lambda _c, _t, _u: {
                "id": "uuid-stale",
                "metadata": {pfs.VAMS_RESERVED_FILE_VERSION_KEY: "v-old"},
            },
        )
        monkeypatch.setattr(
            pfs, "_build_metadata_payload", lambda *a, **k: {"user_key": "v"}
        )
        monkeypatch.setattr(
            pfs._s3, "download_file", MagicMock(side_effect=_no_such_key())
        )
        # Every version purged under every spelling probed.
        monkeypatch.setattr(
            pfs._s3, "list_object_versions", lambda **kwargs: {}
        )

        def _delete(_client, uuid, _full_path):
            state["deletes"].append(uuid)
            if delete_error is not None:
                raise pfs.PhysnaError(delete_error)

        monkeypatch.setattr(pfs, "_delete_physna_asset_by_uuid", _delete)
        monkeypatch.setattr(pfs, "PhysnaClient", lambda *a, **k: MagicMock())
        return pfs, state

    return _wire


DELETE_FAILED = "Delete-for-reupload failed with status 500"


@pytest.mark.unit
class TestReconcileDeleteFailure:
    def test_an_orphan_that_could_not_be_deleted_is_not_acked(self, reconcile):
        pfs, state = reconcile(delete_error=DELETE_FAILED)

        ok = pfs._upload_file_to_physna(DB, ASSET, RELATIVE, BUCKET, S3_KEY)

        assert "uuid-stale" in state["deletes"], (
            "the reconcile-delete must have been attempted"
        )
        assert ok is False, (
            "the Physna copy of a permanently deleted VAMS file is still there and no "
            "later event names the purged key, so acking loses the orphan for good"
        )
        assert (pfs.SYNC_ACTION_DELETE, pfs.SYNC_STATUS_FAILED) in _recorded(
            state["records"]
        ), f"expected a failed delete record, got {_recorded(state['records'])}"
        assert pfs.SYNC_STATUS_SUCCESS not in _statuses(state["records"])

    def test_the_sqs_record_is_reported_for_redrive(self, reconcile):
        pfs, _state = reconcile(delete_error=DELETE_FAILED)

        response = pfs.lambda_handler(_s3_event(), MagicMock())

        assert _identifiers(response) == [MESSAGE_ID]

    def test_a_successful_reconcile_delete_still_acks(self, reconcile):
        """Positive control: converging the two sides is a completed sync."""
        pfs, state = reconcile()

        response = pfs.lambda_handler(_s3_event(), MagicMock())

        assert "uuid-stale" in state["deletes"]
        assert _identifiers(response) == []
        assert (pfs.SYNC_ACTION_DELETE, pfs.SYNC_STATUS_SUCCESS) in _recorded(
            state["records"]
        )
        assert pfs.SYNC_STATUS_FAILED not in _statuses(state["records"])


# ---------------------------------------------------------------------------
# A stale copy Physna would not release.
# ---------------------------------------------------------------------------


@pytest.fixture
def stale_reupload(monkeypatch):
    """Physna holds an out-of-date copy; the delete-before-re-upload is scripted."""
    from backend.backend.handlers.addon.physna import physnaFileSync as pfs

    def _wire(*, delete_error=None, upload_status=201):
        state = {"records": [], "deletes": [], "metadata_calls": []}

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
        monkeypatch.setattr(pfs, "_get_s3_version_id", lambda bucket, key: "v-new")
        monkeypatch.setattr(pfs, "lookup_physna_asset_id", lambda *a: "uuid-old")
        monkeypatch.setattr(
            pfs,
            "get_physna_asset",
            lambda _c, _t, _u: {
                "id": "uuid-old",
                "metadata": {pfs.VAMS_RESERVED_FILE_VERSION_KEY: "v-old"},
            },
        )
        monkeypatch.setattr(
            pfs,
            "_build_metadata_payload",
            lambda db, aid, rel, file_version=None, asset_details=None: (
                {"user_key": "v", pfs.VAMS_RESERVED_FILE_VERSION_KEY: file_version}
                if file_version
                else {"user_key": "v"}
            ),
        )

        def _download(_bucket, _key, local_path):
            with open(local_path, "wb") as handle:
                handle.write(b"new-cad-bytes")

        monkeypatch.setattr(pfs._s3, "download_file", _download)

        def _delete(_client, uuid, _full_path):
            state["deletes"].append(uuid)
            if delete_error is not None:
                raise pfs.PhysnaError(delete_error)

        monkeypatch.setattr(pfs, "_delete_physna_asset_by_uuid", _delete)

        def _update_metadata(_client, _full_path, uuid, payload):
            state["metadata_calls"].append((uuid, payload))
            return True

        monkeypatch.setattr(pfs, "_update_physna_metadata", _update_metadata)

        upload_response = MagicMock()
        upload_response.status = upload_status
        upload_response.data = json.dumps({"id": "uuid-fresh"}).encode("utf-8")
        client = MagicMock()
        client.request.return_value = upload_response
        monkeypatch.setattr(pfs, "PhysnaClient", lambda *a, **k: client)
        return pfs, state

    return _wire


@pytest.mark.unit
class TestStaleCopyPhysnaWouldNotRelease:
    def test_stale_bytes_are_not_labelled_with_the_current_version(
        self, stale_reupload
    ):
        pfs, state = stale_reupload(
            delete_error=DELETE_FAILED, upload_status=409
        )

        ok = pfs._upload_file_to_physna(DB, ASSET, RELATIVE, BUCKET, S3_KEY)

        assert "uuid-old" in state["deletes"], (
            "the delete-before-re-upload must have been attempted"
        )
        assert ok is False
        assert state["metadata_calls"] == [], (
            "the current S3 VersionId must not be written onto a copy that still holds "
            "the previous bytes -- every later staleness check reads that key, so the "
            "divergence would never be revisited"
        )
        assert pfs.SYNC_STATUS_FAILED in _statuses(state["records"])
        assert pfs.SYNC_STATUS_SUCCESS not in _statuses(state["records"])

    def test_the_sqs_record_is_reported_for_redrive(self, stale_reupload):
        pfs, _state = stale_reupload(delete_error=DELETE_FAILED, upload_status=409)

        response = pfs.lambda_handler(_s3_event(), MagicMock())

        assert _identifiers(response) == [MESSAGE_ID]

    def test_an_upload_that_lands_anyway_still_acks(self, stale_reupload):
        """Positive control: a failed stale delete is not fatal on its own.

        Physna accepting the new bytes means the sync achieved what it set out to, so the
        record is acked and the fresh copy carries the current version.
        """
        pfs, state = stale_reupload(delete_error=DELETE_FAILED, upload_status=201)

        response = pfs.lambda_handler(_s3_event(), MagicMock())

        assert _identifiers(response) == []
        assert state["metadata_calls"], "the fresh copy must still get its metadata"
        _uuid, payload = state["metadata_calls"][-1]
        assert payload.get(pfs.VAMS_RESERVED_FILE_VERSION_KEY) == "v-new"
        assert pfs.SYNC_STATUS_FAILED not in _statuses(state["records"])

    def test_a_409_after_a_successful_delete_still_acks(self, stale_reupload):
        """Positive control: the plain already-exists path is unchanged.

        With the stale copy actually deleted, a 409 means something else put the file
        there, so the metadata refresh is the right finish.
        """
        pfs, state = stale_reupload(upload_status=409)

        response = pfs.lambda_handler(_s3_event(), MagicMock())

        assert "uuid-old" in state["deletes"]
        assert _identifiers(response) == []
        assert state["metadata_calls"], "the copy at the path must still get its metadata"
        assert pfs.SYNC_STATUS_FAILED not in _statuses(state["records"])
