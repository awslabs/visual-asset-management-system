# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""One Physna copy Physna will not delete must not strand the asset's other copies.

``_delete_by_item`` raises ``PhysnaError`` for any DELETE status outside (200, 204, 404),
and ``PhysnaClient.request`` returns a rate limit as-is rather than retrying it, so a 429
part-way through a large asset is a raise the loop over the listing has to survive. When
neither call site contained it, the first such status ended the walk: every copy after it
stayed in the tenant untouched, nothing after the loop ran, and the one record written named
the asset rather than the files. A redrive re-attempts the same failing copy first, so it
never reaches them either -- after ``maxReceiveCount`` the message goes to the DLQ with an
arbitrary subset of the asset's files still resident in the customer's tenant.

The other shortfall on the same route -- a listing item carrying no addressable UUID -- is
covered by ``test_physnaAssetSyncHalfFailureAck.py``; this file is about the raise.

Both call sites are exercised: the permanent-delete loop, and the orphan prune inside the
per-file loop of a re-sync. Each failure case is paired with a positive control that must
still ack, because a try/except that contained the raise and then acked the record would be
worse than the abort it replaced.
"""

import json

import pytest
from unittest.mock import MagicMock

# Module-level import ensures the real `backend.backend.handlers` package is populated
# in sys.modules before the root conftest's autouse fixture runs.
from backend.backend.handlers.addon.physna import physnaAssetSync as _pas  # noqa: F401

DB = "db-1"
ASSET = "asset-1"
PREFIX = f"{DB}/{ASSET}/"
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
                    {"Type": "Notification", "Message": json.dumps(stream_record)}
                ),
            }
        ]
    }


def _item(relative, uuid=None, metadata=None):
    """A Physna listing item for one copy under this asset's folder."""
    item = {"path": PREFIX + relative.lstrip("/")}
    if uuid:
        item["id"] = uuid
    if metadata is not None:
        item["metadata"] = metadata
    return item


def _asset_statuses(records):
    """The set of ``syncStatus`` values written for the ASSET, by position or keyword.

    ``_record_asset_sync(database_id, asset_id, action, sync_status, error_message=None)``.
    """
    out = set()
    for args, kwargs in records:
        out.add(args[3] if len(args) > 3 else kwargs.get("sync_status"))
    return out


def _file_failures(records):
    """``{(filePath, action, syncStatus)}`` for the file-level records written.

    ``_record_file_sync(database_id, asset_id, relative_path, action, sync_status, ...)``.
    Set containment, never call counts: an implementation that also recorded the copies it
    deleted successfully is not worse and must not fail here.
    """
    out = set()
    for args, kwargs in records:
        out.add(
            (
                args[2] if len(args) > 2 else kwargs.get("relative_path"),
                args[3] if len(args) > 3 else kwargs.get("action"),
                args[4] if len(args) > 4 else kwargs.get("sync_status"),
            )
        )
    return out


@pytest.fixture
def harness(monkeypatch):
    """Wire the collaborators of the asset sync and capture what it deletes and records."""
    from backend.backend.handlers.addon.physna import physnaAssetSync as pas

    def _wire(
        *,
        physna_items=(),
        vams_paths=(),
        delete_statuses=None,
        asset_present=True,
        files_still_in_s3=None,
    ):
        """``delete_statuses`` maps a Physna asset UUID to the status its DELETE answers;
        a UUID it does not name answers 204. ``files_still_in_s3`` is what
        ``_vams_file_still_in_s3`` answers, which is what decides whether an unindexed copy
        is pruned (False = every version purged = prune it)."""
        statuses = dict(delete_statuses or {})
        state = {
            "asset_records": [],
            "file_records": [],
            "requests": [],
            "folders": [],
            "uploads": [],
        }

        monkeypatch.setattr(
            pas,
            "_record_asset_sync",
            lambda *args, **kwargs: state["asset_records"].append((args, kwargs)),
        )
        monkeypatch.setattr(
            pas.physnaFileSync,
            "_record_file_sync",
            lambda *args, **kwargs: state["file_records"].append((args, kwargs)),
        )
        monkeypatch.setattr(
            pas,
            "get_asset_details",
            lambda database_id, asset_id: (
                {
                    "assetName": "My Asset",
                    "bucketId": "b-1",
                    "assetLocation": {"Key": ASSET_BASE_KEY},
                }
                if asset_present
                else None
            ),
        )
        monkeypatch.setattr(
            pas,
            "get_bucket_details",
            lambda _bucket_id: {"bucketName": BUCKET, "baseAssetsPrefix": "prefix/"},
        )
        monkeypatch.setattr(pas, "get_asset_metadata", lambda _db, _asset: {})
        monkeypatch.setattr(pas, "get_file_metadata", lambda _db, _asset, _rel: ({}, {}))
        monkeypatch.setattr(
            pas, "list_physna_assets_under", lambda *a, **k: iter(list(physna_items))
        )
        monkeypatch.setattr(
            pas, "_list_vams_file_paths", lambda _db, _asset: set(vams_paths)
        )
        monkeypatch.setattr(pas, "_prefetch_file_metadata", lambda _db, _asset: {})
        monkeypatch.setattr(pas, "ensure_metadata_fields_registered", lambda *a, **k: None)
        monkeypatch.setattr(pas, "delete_physna_metadata_fields", lambda *a, **k: None)
        monkeypatch.setattr(
            pas,
            "delete_folder_if_empty",
            lambda _client, _tenant, folder: state["folders"].append(folder),
        )
        monkeypatch.setattr(
            pas.physnaFileSync,
            "_vams_file_still_in_s3",
            lambda _bucket, _keys: files_still_in_s3,
        )

        def _upload(database_id, asset_id, relative, bucket_name, s3_key, client=None):
            state["uploads"].append(relative)
            return True

        monkeypatch.setattr(pas.physnaFileSync, "_upload_file_to_physna", _upload)

        def _request(method, url, **_kwargs):
            state["requests"].append((method, url))
            response = MagicMock()
            response.data = b'{"error": "injected"}'
            if method == "DELETE":
                response.status = statuses.get(url.rsplit("/", 1)[-1], 204)
            else:
                response.status = 204
            return response

        client = MagicMock()
        client.request.side_effect = _request
        monkeypatch.setattr(pas, "PhysnaClient", lambda *a, **k: client)
        return pas, state

    return _wire


def _deleted_uuids(state):
    """The UUIDs a DELETE was actually issued for, as a set rather than a call sequence."""
    return {
        url.rsplit("/", 1)[-1]
        for method, url in state["requests"]
        if method == "DELETE"
    }


@pytest.mark.unit
class TestPermanentDeleteLoopIsolation:
    """The loop that removes every Physna copy of an asset deleted from VAMS."""

    ITEMS = [
        _item("/a.step", "uuid-a"),
        _item("/b.step", "uuid-b"),
        _item("/sub/c.step", "uuid-c"),
    ]

    def _run(self, harness, delete_statuses=None):
        pas, state = harness(
            physna_items=self.ITEMS,
            vams_paths=set(),
            delete_statuses=delete_statuses,
            asset_present=False,
        )
        response = pas.lambda_handler(_asset_sqs_event(event_name="REMOVE"), MagicMock())
        return pas, state, response

    def test_every_listed_copy_is_still_deleted_when_one_delete_fails(self, harness):
        """A 429 on the second copy must not leave the third resident in the tenant."""
        _pas_mod, state, _response = self._run(harness, delete_statuses={"uuid-b": 429})

        assert {"uuid-a", "uuid-b", "uuid-c"} <= _deleted_uuids(state), (
            f"the failing copy ended the walk; DELETEs were issued for "
            f"{_deleted_uuids(state)}"
        )

    def test_the_copy_that_survived_is_named_in_its_own_failed_record(self, harness):
        """The asset-level record says only that something fell short. Nothing else ever
        names the file again -- the VAMS asset is gone under both keys."""
        pas, state, _response = self._run(harness, delete_statuses={"uuid-b": 429})

        assert ("/b.step", pas.SYNC_ACTION_DELETE, pas.SYNC_STATUS_FAILED) in (
            _file_failures(state["file_records"])
        ), f"expected a file-level failed record for /b.step, got {state['file_records']}"

    def test_the_surviving_copy_carries_the_uuid_it_is_addressed_by(self, harness):
        """Physna's asset-scoped endpoints are keyed by UUID, not by path, so the UUID is
        what an operator removes the leftover copy by. ``physnaFileSync`` records it on its
        own failed rows for the same reason."""
        _pas_mod, state, _response = self._run(harness, delete_statuses={"uuid-b": 429})

        recorded = {
            kwargs.get("physna_asset_uuid") for _args, kwargs in state["file_records"]
        }
        assert "uuid-b" in recorded, (
            f"the record names the path but not the handle the copy is addressed by; "
            f"recorded {state['file_records']}"
        )

    def test_the_work_after_the_loop_is_still_reached(self, harness):
        """What follows the loop is the asset-folder cleanup and the return of
        ``delete_complete``. The shortfall message is the observable for "the walk
        finished": only the return path writes it, so a raise that ended the walk records
        the DELETE status instead."""
        _pas_mod, state, _response = self._run(harness, delete_statuses={"uuid-b": 429})

        assert any(
            "did not land" in (kwargs.get("error_message") or "")
            for _args, kwargs in state["asset_records"]
        ), (
            f"the record carries the raised DELETE status rather than the shortfall the "
            f"return path reports; records {state['asset_records']}"
        )
        # The asset folder is the only cleanup target -- the database folder holds the
        # database's other assets.
        assert PREFIX.rstrip("/") in state["folders"] and DB not in state["folders"], (
            f"folders reached {state['folders']}"
        )

    def test_the_record_is_still_reported_for_redrive(self, harness):
        """Containing the raise must not turn the failure into an ack. This held before the
        isolation too -- the exception reached the handler -- and it is what must not
        regress now that the exception no longer travels."""
        pas, state, response = self._run(harness, delete_statuses={"uuid-b": 429})

        assert {"itemIdentifier": MESSAGE_ID} in response["batchItemFailures"]
        assert pas.SYNC_STATUS_FAILED in _asset_statuses(state["asset_records"])
        assert pas.SYNC_STATUS_SUCCESS not in _asset_statuses(state["asset_records"])

    def test_a_clean_delete_of_every_copy_still_acks(self, harness):
        """Positive control: the ordinary permanent delete is untouched."""
        pas, state, response = self._run(harness)

        assert {"uuid-a", "uuid-b", "uuid-c"} <= _deleted_uuids(state)
        assert response["batchItemFailures"] == []
        assert _asset_statuses(state["asset_records"]) == {pas.SYNC_STATUS_SUCCESS}
        assert state["file_records"] == [], (
            "no copy fell short, so nothing may be recorded as a failed file"
        )


@pytest.mark.unit
class TestOrphanPruneIsolation:
    """The prune of a Physna copy whose VAMS object S3 shows every version purged.

    It sits inside the per-file loop of a re-sync, so an abort there also skips the metadata
    re-sync of the remaining files and the repair upload that runs after the loop.
    """

    ITEMS = [
        _item("/orphan-a.step", "uuid-orphan-a"),
        _item("/orphan-b.step", "uuid-orphan-b"),
    ]
    # A VAMS file the narrowed listing did not return, so the repair upload after the loop
    # has something to attempt. It is the observable that shows the loop ran to its end.
    NEEDS_UPLOAD = "/housing/pump.stl"

    def _run(self, harness, delete_statuses=None):
        pas, state = harness(
            physna_items=self.ITEMS,
            vams_paths={self.NEEDS_UPLOAD},
            delete_statuses=delete_statuses,
            files_still_in_s3=False,
        )
        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())
        return pas, state, response

    def test_a_failing_prune_does_not_abandon_the_remaining_copies(self, harness):
        _pas_mod, state, _response = self._run(
            harness, delete_statuses={"uuid-orphan-a": 429}
        )

        assert {"uuid-orphan-a", "uuid-orphan-b"} <= _deleted_uuids(state), (
            f"the failing prune ended the per-file loop; DELETEs were issued for "
            f"{_deleted_uuids(state)}"
        )

    def test_the_repair_upload_after_the_loop_is_still_reached(self, harness):
        _pas_mod, state, _response = self._run(
            harness, delete_statuses={"uuid-orphan-a": 429}
        )

        assert self.NEEDS_UPLOAD in state["uploads"], (
            f"the work after the per-file loop was skipped; uploads {state['uploads']}"
        )

    def test_the_pruned_copy_that_survived_is_named_and_the_record_reported(self, harness):
        pas, state, response = self._run(harness, delete_statuses={"uuid-orphan-a": 429})

        assert ("/orphan-a.step", pas.SYNC_ACTION_DELETE, pas.SYNC_STATUS_FAILED) in (
            _file_failures(state["file_records"])
        ), f"expected a file-level failed record, got {state['file_records']}"
        assert {"itemIdentifier": MESSAGE_ID} in response["batchItemFailures"]
        assert pas.SYNC_STATUS_SUCCESS not in _asset_statuses(state["asset_records"])

    def test_a_clean_prune_still_acks(self, harness):
        """Positive control for the same branch: both copies pruned, the repair upload run,
        and the record acked."""
        pas, state, response = self._run(harness)

        assert {"uuid-orphan-a", "uuid-orphan-b"} <= _deleted_uuids(state)
        assert self.NEEDS_UPLOAD in state["uploads"]
        assert response["batchItemFailures"] == []
        assert _asset_statuses(state["asset_records"]) == {pas.SYNC_STATUS_SUCCESS}
        assert state["file_records"] == []
