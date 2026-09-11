# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The asset-sync route that uploads nothing must not ack a metadata write that failed.

An asset-metadata edit is the most common asset-sync trigger and it takes a route that
performs NO upload: the per-file loop finds a Physna copy that already carries
``__VAMS__FileVersion``, prunes the keys VAMS has dropped, and PATCHes the rest. Every one
of those writes used to be logged-and-continued while the invocation still answered
"complete", so the SQS record was acked, SQS deleted it, and the asset's sync-tracking
record asserted ``success`` for metadata Physna never received.
``test_physnaUploadHalfFailureAck.py`` closed that for the file-sync queue and
``test_physnaAssetSyncHalfFailureAck.py`` for the asset sync's two upload call sites; this
file closes the route that reaches neither of them.

Three writes on that route can fail, and all three are the same shortfall -- Physna keeps
metadata VAMS no longer has, or never receives what it does:

* the metadata PATCH answering a status outside (200, 204, 404),
* the full-replace prune of stale keys raising,
* a listing item carrying no UUID, so the file's metadata cannot be addressed at all.

Metadata-field pre-registration is deliberately NOT one of them: it carries no data of its
own, and an unregistered field makes the PATCH itself answer a bad status. Both halves of
that carve-out are asserted in ``TestFieldRegistrationIsNotACountedWrite`` rather than
assumed, because an unexplained exemption is what invites the next author to remove it.

Every redrive assertion drives the real ``lambda_handler`` with an SQS -> SNS -> DynamoDB
stream payload, because ``batchItemFailures`` is what decides redrive and the event source
mapping is configured with ``reportBatchItemFailures``. Each failure case is paired with a
positive control that must still ack, so "everything fails now" cannot pass.

``TestEveryMutatingPhysnaCallIsCounted`` is the class guard rather than a per-branch test:
it discovers the mutating Physna calls this route actually issues from a clean run, then
requires each one, failed on its own, to report the record. A mutation added to this route
later is covered without editing this file.
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
ASSET_NAME = "My Asset"
FILE_VERSION = "ver-1"

# What the sync computes for a file from the VAMS state this module wires: the asset-level
# metadata row, plus the two VAMS-reserved tracking keys. Written out rather than derived
# from the handler's own helpers so the expectation is independent of them.
TARGET_METADATA = {
    "partFamily": "widgets",
    "__VAMS__AssetName": ASSET_NAME,
    "__VAMS__FileVersion": FILE_VERSION,
}

# The verbs that change Physna state. A failed read can be legitimately recoverable; a
# failed mutation always means the intended Physna state was not reached, which is what
# the class guard sweeps over.
MUTATING_LABELS = {"PATCH", "DELETE_METADATA", "DELETE_ASSET", "POST"}


class _FakeResponse:
    def __init__(self, status=204, data=b""):
        self.status = status
        self.data = data


class _FakePhysna:
    """One Physna tenant: which paths exist, their metadata, and every call received.

    ``failures`` names the calls that answer a failing status. A member is either a coarse
    label (``"PATCH"`` -- every PATCH fails) or a ``(label, path)`` pair (only that file's
    call fails), which is what lets a test fail one file of an asset and assert the others
    still landed.

    ``statuses`` answers a specific status for a label or ``(label, path)`` instead, which
    is how the 404 case ("the Physna copy is already gone") is distinguished from a real
    write failure.
    """

    def __init__(self, paths_metadata, failures=(), statuses=None):
        self.assets = {path: dict(meta) for path, meta in paths_metadata.items()}
        self.uuid_by_path = {p: f"uuid-{i}" for i, p in enumerate(sorted(self.assets))}
        self.calls = []
        self.failures = set(failures)
        self.statuses = dict(statuses or {})

    # -- client seam --------------------------------------------------------
    def client(self):
        client = MagicMock()
        client.request = MagicMock(side_effect=self._request)
        return client

    def _label_and_path(self, method, url):
        if method == "DELETE" and url.endswith("/metadata"):
            return "DELETE_METADATA", self._path_for(url[: -len("/metadata")])
        if method == "DELETE":
            return "DELETE_ASSET", self._path_for(url)
        if method == "POST":
            return "POST", url
        return method, self._path_for(url)

    def _path_for(self, url):
        uuid = url.rsplit("/", 1)[-1]
        return next((p for p, known in self.uuid_by_path.items() if known == uuid), None)

    def _request(self, method, url, **kwargs):
        label, path = self._label_and_path(method, url)
        self.calls.append((label, path))
        override = self.statuses.get((label, path), self.statuses.get(label))
        if override is not None:
            return _FakeResponse(status=override, data=b'{"error": "injected"}')
        if label in self.failures or (label, path) in self.failures:
            return _FakeResponse(status=500, data=b'{"error": "injected"}')
        if label == "PATCH" and path is not None:
            body = json.loads(kwargs["body"].decode("utf-8"))
            self.assets[path] = dict(body["metadata"])
        elif label == "DELETE_METADATA" and path is not None:
            names = json.loads(kwargs["body"].decode("utf-8"))["metadataFieldNames"]
            for name in names:
                self.assets[path].pop(name, None)
        elif label == "DELETE_ASSET" and path is not None:
            self.assets.pop(path, None)
            self.uuid_by_path.pop(path, None)
        return _FakeResponse()

    # -- listing seam -------------------------------------------------------
    def listing(self, *_args, **_kwargs):
        return iter(
            [
                {"id": self.uuid_by_path[path], "path": path, "metadata": dict(meta)}
                for path, meta in sorted(self.assets.items())
            ]
        )

    # -- assertions ---------------------------------------------------------
    def labels(self):
        return {label for label, _path in self.calls}

    def mutating_calls(self):
        return {
            (label, path) for label, path in self.calls if label in MUTATING_LABELS
        }


def _physna_state(relatives, metadata):
    """A Physna tenant holding ``relatives`` under this asset's folder."""
    return {f"{PREFIX}{r.lstrip('/')}": dict(metadata) for r in relatives}


# The Physna copy a metadata edit finds: current bytes (so the version tag is present and
# no upload is triggered), a stale value for the edited key, and a key VAMS has dropped
# (so the full-replace prune runs too).
STALE_VALUE_AND_STALE_KEY = {
    "partFamily": "sprockets",
    "legacyKey": "dropped-in-vams",
    "__VAMS__AssetName": ASSET_NAME,
    "__VAMS__FileVersion": FILE_VERSION,
}

# The same copy with nothing to prune, so the PATCH is the route's only mutation.
STALE_VALUE_ONLY = {
    "partFamily": "sprockets",
    "__VAMS__AssetName": ASSET_NAME,
    "__VAMS__FileVersion": FILE_VERSION,
}


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
    so action and status are the third and fourth positional arguments; either may instead
    arrive by keyword. An implementation that writes an extra tracking record, or writes
    them in another order, is not worse and must not fail here.
    """
    out = set()
    for args, kwargs in records:
        action = args[2] if len(args) > 2 else kwargs.get("action")
        status = args[3] if len(args) > 3 else kwargs.get("sync_status")
        out.add((action, status))
    return out


def _statuses(records):
    return {status for _action, status in _recorded(records)}


def _reported(response):
    return {"itemIdentifier": MESSAGE_ID} in response["batchItemFailures"]


@pytest.fixture
def harness(monkeypatch):
    """Wire the asset re-sync to an in-memory Physna tenant and capture what it records."""
    from backend.backend.handlers.addon.physna import physnaAssetSync as pas

    def _wire(
        *,
        physna_metadata=None,
        relatives=("/part.step",),
        physna_relatives=None,
        failures=(),
        statuses=None,
        registration_error=None,
        drop_listing_ids=False,
    ):
        physna = _FakePhysna(
            _physna_state(
                relatives if physna_relatives is None else physna_relatives,
                STALE_VALUE_ONLY if physna_metadata is None else physna_metadata,
            ),
            failures=failures,
            statuses=statuses,
        )
        state = {"records": [], "uploads": [], "physna": physna}

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
                if "#deleted" in database_id
                else {
                    "assetName": ASSET_NAME,
                    "bucketId": "b-1",
                    "assetLocation": {"Key": ASSET_BASE_KEY},
                }
            ),
        )
        monkeypatch.setattr(
            pas,
            "get_bucket_details",
            lambda _bucket_id: {"bucketName": BUCKET, "baseAssetsPrefix": "prefix/"},
        )
        # One asset-level metadata key, in the row shape physnaCommon.merge_metadata
        # expects. merge_metadata / physna_format_metadata / apply_vams_reserved_metadata
        # are the real helpers, so TARGET_METADATA is what they really produce.
        monkeypatch.setattr(
            pas,
            "get_asset_metadata",
            lambda _db, _asset: {"partFamily": {"value": "widgets", "type": "string"}},
        )
        monkeypatch.setattr(pas, "get_file_metadata", lambda *a, **k: ({}, {}))
        monkeypatch.setattr(pas, "_prefetch_file_metadata", lambda _db, _asset: {})
        monkeypatch.setattr(
            pas, "_list_vams_file_paths", lambda _db, _asset: set(relatives)
        )
        monkeypatch.setattr(pas, "delete_folder_if_empty", lambda *a, **k: None)

        def _register(*_args, **_kwargs):
            if registration_error is not None:
                raise registration_error

        monkeypatch.setattr(pas, "ensure_metadata_fields_registered", _register)

        def _upload(database_id, asset_id, relative, bucket_name, s3_key, client=None):
            state["uploads"].append((relative, bucket_name, s3_key))
            return True

        monkeypatch.setattr(pas.physnaFileSync, "_upload_file_to_physna", _upload)

        client = physna.client()
        monkeypatch.setattr(pas, "PhysnaClient", lambda *a, **k: client)

        if drop_listing_ids:
            # A listing item with no addressable UUID under any of the three spellings
            # the handler accepts.
            def _listing_without_ids(*_a, **_k):
                return iter(
                    [
                        {"path": path, "metadata": dict(meta)}
                        for path, meta in sorted(physna.assets.items())
                    ]
                )

            monkeypatch.setattr(pas, "list_physna_assets_under", _listing_without_ids)
        else:
            monkeypatch.setattr(pas, "list_physna_assets_under", physna.listing)

        return pas, state

    return _wire


@pytest.mark.unit
class TestTheRouteUnderTest:
    """Establish that these cases really are the no-upload route.

    Without this, every assertion below could be passing because it silently took the
    upload path that ``test_physnaAssetSyncHalfFailureAck.py`` already covers.
    """

    def test_a_metadata_edit_patches_and_uploads_nothing(self, harness):
        pas, state = harness()

        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert state["uploads"] == [], (
            "this route must reach no upload call site, or the failure assertions in this "
            "file are really re-testing the upload path"
        )
        assert ("PATCH", f"{PREFIX}part.step") in state["physna"].mutating_calls()
        assert response["batchItemFailures"] == []

    def test_the_patch_writes_what_vams_holds(self, harness):
        """The route's own contract, so a redrive assertion cannot be passing because the
        payload was malformed rather than because the write failed."""
        pas, state = harness()

        pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert state["physna"].assets[f"{PREFIX}part.step"] == TARGET_METADATA


@pytest.mark.unit
class TestMetadataPatchFails:
    """The PATCH answering a status outside (200, 204, 404)."""

    def test_the_record_is_reported_for_redrive(self, harness):
        pas, state = harness(failures={"PATCH"})

        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert "PATCH" in state["physna"].labels(), (
            "the PATCH must have been attempted, or the assertion below is vacuous"
        )
        assert _reported(response), (
            "a metadata write that never landed must be redriven, not deleted"
        )

    def test_the_sync_record_does_not_claim_success(self, harness):
        pas, state = harness(failures={"PATCH"})

        pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert (pas.SYNC_ACTION_MODIFY, pas.SYNC_STATUS_FAILED) in _recorded(
            state["records"]
        ), f"expected a failed asset record, got {_recorded(state['records'])}"
        assert pas.SYNC_STATUS_SUCCESS not in _statuses(state["records"]), (
            "an asset sync whose metadata never reached Physna must not be recorded as a "
            "success"
        )

    def test_one_file_s_failure_does_not_abandon_the_others(self, harness):
        """The asset is still walked, and the record is still reported."""
        pas, state = harness(
            relatives=("/a.step", "/b.step", "/c.step"),
            failures={("PATCH", f"{PREFIX}b.step")},
        )

        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        physna = state["physna"]
        expected = {
            ("PATCH", f"{PREFIX}{name}") for name in ("a.step", "b.step", "c.step")
        }
        assert expected <= physna.mutating_calls(), (
            f"a failure stopped the remaining files; calls were "
            f"{sorted(map(repr, physna.calls))}"
        )
        assert physna.assets[f"{PREFIX}a.step"] == TARGET_METADATA
        assert physna.assets[f"{PREFIX}c.step"] == TARGET_METADATA
        assert _reported(response)

    def test_a_404_is_not_a_failure(self, harness):
        """A 404 means the Physna copy is already gone, so there is nothing to write and
        nothing a redrive could achieve. The status list is what distinguishes the two, so
        widening the fix to every non-2xx would be wrong."""
        pas, state = harness(statuses={"PATCH": 404})

        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert "PATCH" in state["physna"].labels(), "the PATCH must have been attempted"
        assert response["batchItemFailures"] == []
        assert pas.SYNC_STATUS_FAILED not in _statuses(state["records"])

    def test_a_successful_patch_still_acks(self, harness):
        """Positive control: the batch must still drain when the write lands."""
        pas, state = harness()

        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert "PATCH" in state["physna"].labels(), (
            "the control must exercise the same write"
        )
        assert response["batchItemFailures"] == []
        assert (pas.SYNC_ACTION_MODIFY, pas.SYNC_STATUS_SUCCESS) in _recorded(
            state["records"]
        )
        assert pas.SYNC_STATUS_FAILED not in _statuses(state["records"])


@pytest.mark.unit
class TestStaleKeyPruneFails:
    """The full-replace prune: Physna keeping a value VAMS has dropped."""

    def test_the_record_is_reported_for_redrive(self, harness):
        pas, state = harness(
            physna_metadata=STALE_VALUE_AND_STALE_KEY,
            failures={"DELETE_METADATA"},
        )

        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert "DELETE_METADATA" in state["physna"].labels(), (
            "the prune must have been attempted, or the assertion below is vacuous"
        )
        assert _reported(response)
        assert pas.SYNC_STATUS_SUCCESS not in _statuses(state["records"])

    def test_the_patch_still_runs_so_the_values_vams_has_still_land(self, harness):
        """A failed prune must not cost the file its metadata update as well -- the
        remaining keys are still written, and only the ack changes."""
        pas, state = harness(
            physna_metadata=STALE_VALUE_AND_STALE_KEY,
            failures={"DELETE_METADATA"},
        )

        pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert state["physna"].assets[f"{PREFIX}part.step"] == TARGET_METADATA

    def test_a_successful_prune_still_acks(self, harness):
        """Positive control for the branch above."""
        pas, state = harness(physna_metadata=STALE_VALUE_AND_STALE_KEY)

        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert "DELETE_METADATA" in state["physna"].labels()
        assert response["batchItemFailures"] == []
        assert pas.SYNC_STATUS_FAILED not in _statuses(state["records"])
        assert "legacyKey" not in state["physna"].assets[f"{PREFIX}part.step"]


@pytest.mark.unit
class TestListingItemWithNoAddressableUuid:
    """Physna's metadata endpoints are addressed by UUID, so an item without one cannot be
    written at all -- the same shortfall as a failed PATCH, one step earlier."""

    def test_the_record_is_reported_for_redrive(self, harness):
        pas, state = harness(drop_listing_ids=True)

        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert state["physna"].mutating_calls() == set(), (
            "this branch exists precisely because no write can be issued"
        )
        assert _reported(response)
        assert pas.SYNC_STATUS_SUCCESS not in _statuses(state["records"])

    def test_an_addressable_item_still_acks(self, harness):
        """Positive control: the same asset, listed with its UUID."""
        pas, state = harness(drop_listing_ids=False)

        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert response["batchItemFailures"] == []
        assert pas.SYNC_STATUS_FAILED not in _statuses(state["records"])


@pytest.mark.unit
class TestFieldRegistrationIsNotACountedWrite:
    """The one Physna call whose failure is deliberately not a shortfall.

    Pre-registration carries no data of its own: it only declares field names, and an
    unregistered field makes the PATCH answer a bad status. So a registration failure whose
    PATCH still lands leaves Physna holding exactly what VAMS holds, and redriving it would
    achieve nothing. Both halves are asserted so the exemption cannot silently widen into
    "metadata failures on this route are ignored again".
    """

    def test_a_registration_failure_whose_patch_lands_still_acks(self, harness):
        pas, state = harness(registration_error=RuntimeError("field API unavailable"))

        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert response["batchItemFailures"] == []
        assert state["physna"].assets[f"{PREFIX}part.step"] == TARGET_METADATA, (
            "the exemption is only sound while the PATCH still writes the metadata"
        )
        assert pas.SYNC_STATUS_FAILED not in _statuses(state["records"])

    def test_a_registration_failure_that_costs_the_patch_is_reported(self, harness):
        """Physna rejecting an unregistered field is what the exemption relies on being
        caught downstream, so it must be."""
        pas, state = harness(
            registration_error=RuntimeError("field API unavailable"),
            failures={"PATCH"},
        )

        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert _reported(response)
        assert pas.SYNC_STATUS_SUCCESS not in _statuses(state["records"])


@pytest.mark.unit
class TestRedriveConverges:
    """What the second delivery does, since a reported record is redelivered.

    A redrive re-attempts whatever already succeeded, so the route has to be idempotent for
    the report to be an improvement: the target metadata is re-derived from VAMS state and a
    file Physna already holds it for is skipped without a write. Asserted as an end state
    rather than as a call count, so an implementation that harmlessly re-writes a file that
    was already correct still passes.
    """

    def test_the_second_delivery_repairs_the_file_that_failed(self, harness):
        pas, state = harness(
            relatives=("/a.step", "/b.step"),
            failures={("PATCH", f"{PREFIX}b.step")},
        )
        physna = state["physna"]

        first = pas.lambda_handler(_asset_sqs_event(), MagicMock())
        assert _reported(first), "the first delivery must be the one that is redriven"
        assert physna.assets[f"{PREFIX}b.step"] != TARGET_METADATA

        # The redelivery: the same message, with Physna healthy again.
        physna.failures = set()
        second = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert second["batchItemFailures"] == [], (
            "the redelivery must be able to drain once Physna accepts the write"
        )
        assert physna.assets == {
            f"{PREFIX}a.step": TARGET_METADATA,
            f"{PREFIX}b.step": TARGET_METADATA,
        }, (
            "two deliveries must converge on the same tenant state a single clean run "
            f"produces; got {physna.assets}"
        )
        assert (pas.SYNC_ACTION_MODIFY, pas.SYNC_STATUS_SUCCESS) in _recorded(
            state["records"]
        )

    def test_a_second_delivery_of_an_already_complete_sync_changes_nothing(
        self, harness
    ):
        """The redrive cost in the ordinary case: the file Physna already holds the target
        payload for is skipped, so a duplicated delivery is not a duplicated rewrite. This
        is the established no-op contract (``test_physnaAssetSync_archive.py``'s
        ``test_unchanged_metadata_issues_no_physna_writes``) restated for the redelivery
        the reported record now causes -- it is what makes the report an improvement rather
        than a per-delivery rewrite of the whole asset."""
        pas, state = harness(relatives=("/a.step", "/b.step"))
        physna = state["physna"]

        pas.lambda_handler(_asset_sqs_event(), MagicMock())
        after_first = {path: dict(meta) for path, meta in physna.assets.items()}
        physna.calls.clear()

        response = pas.lambda_handler(_asset_sqs_event(), MagicMock())

        assert physna.mutating_calls() == set(), (
            f"the second delivery rewrote a file already in the target state: "
            f"{sorted(map(repr, physna.calls))}"
        )
        assert physna.assets == after_first
        assert response["batchItemFailures"] == []


@pytest.mark.unit
class TestEveryMutatingPhysnaCallIsCounted:
    """The class guard: no mutating Physna call on this route may fail silently.

    Rather than naming the writes, this discovers them from a clean run and then fails each
    one on its own. A mutation added to this route later is swept in automatically, so the
    guard fails when the class reappears instead of when someone remembers to extend a list.

    Metadata-field pre-registration is outside the sweep by construction -- the harness
    stubs it, because its failure is deliberately not a shortfall
    (``TestFieldRegistrationIsNotACountedWrite`` asserts both halves of that). Reads are
    outside it too: a failed read can be legitimately recoverable, while a failed mutation
    always means the intended Physna state was not reached.
    """

    def test_each_mutating_call_failed_alone_reports_the_record(self, harness):
        discovery_pas, discovery_state = harness(
            physna_metadata=STALE_VALUE_AND_STALE_KEY
        )
        clean = discovery_pas.lambda_handler(_asset_sqs_event(), MagicMock())
        assert clean["batchItemFailures"] == [], "the clean run must ack"
        discovered = {label for label, _path in discovery_state["physna"].mutating_calls()}

        assert {"PATCH", "DELETE_METADATA"} <= discovered, (
            f"the sweep found no writes to fail; discovered {discovered}. Either the "
            f"harness stopped reaching the metadata route or the route stopped writing."
        )

        for label in sorted(discovered):
            pas, state = harness(
                physna_metadata=STALE_VALUE_AND_STALE_KEY, failures={label}
            )
            response = pas.lambda_handler(_asset_sqs_event(), MagicMock())
            assert label in state["physna"].labels(), (
                f"{label} was never issued under injection, so its case is vacuous"
            )
            assert _reported(response), (
                f"a failed {label} on the asset-sync metadata route was acked; the SQS "
                f"record is deleted and only a log line records the loss"
            )
            assert pas.SYNC_STATUS_SUCCESS not in _statuses(state["records"]), (
                f"a failed {label} was recorded as a successful asset sync"
            )
