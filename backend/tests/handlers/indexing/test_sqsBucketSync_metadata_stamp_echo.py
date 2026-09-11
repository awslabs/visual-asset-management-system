# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`sqsBucketSync` must not treat its OWN metadata stamp as a new file event.

`update_s3_metadata` stamps `databaseid` / `assetid` / `vams-changesource` onto an object by copying
it onto itself. That copy is an object write, so Amazon S3 raises a second `ObjectCreated:Copy`
notification for a file that only ever changed once, and every consumer downstream of this handler
acted on it: the indexers re-indexed, and `asset.file.uploaded` was published a second time so a
`fileUpload` workflow trigger fired TWICE for one written file.

Two things this suite deliberately does NOT claim, both because they were measured and turned out
false:

*   **The history row does not duplicate.** The second pass makes no further copy, so it reads the
    same `VersionId` and `put_item` overwrites the same PK+SK. The waste is a redundant `head_object`
    and write, not a phantom file version.
*   **A VAMS upload never produced an echo at all.** `uploadFile` stamps the same three keys itself,
    so `update_s3_metadata` takes its "metadata already matches" branch and never copies. The echo
    belongs to objects arriving UNSTAMPED — a direct S3 write, a folder marker, a pipeline output
    landing on an asset path. Confirmed live on the deployment: a CLI upload of one file produced one
    notification and one indexer publish; an `s3 cp` of the same content produced two notifications,
    the second of which this guard withholds.

The doubling is also what makes a burst dangerous. Measured on a live deployment: 27,729 notifications
in one hour, after which 15 messages sat permanently in flight on `bucketSyncCreated--0` — received
60-80 times an hour, deleted zero times, `Errors` and `Throttles` flat at zero, and no log line,
because AWS Lambda's recursive-loop detector was dropping the invocations before the handler ran and a
dropped invocation never deletes its message. Every other Lambda in the workflow chain
(`executeWorkflow`, `workflowTriggerDispatch`, `processWorkflowExecutionOutput`,
`registerPipelineExecution`, the indexer queuing functions) reported ZERO drops over the same window,
which is what rules out a workflow-trigger loop and attributes the recursion to this handler's own
write.

The discriminator has to be exact in BOTH directions, and the negative arms below are the reason it is
written as "an ObjectCreated:Copy performed by this function" rather than anything cheaper:

*   Keying on "the object's metadata already matches" would discard every VAMS upload — `uploadFile`
    stamps the same three keys at upload time, so a real upload arrives already matching.
*   Keying on `ObjectCreated:Copy` alone would discard a file COPY or MOVE done by the file-operations
    handlers, which IS a genuine content change at the destination key.
"""

from unittest.mock import MagicMock

import pytest

from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load

# _load() caches the module across test files, so anything replaced here must be restored or the
# mock leaks into the other sqsBucketSync suites.
_PATCHED_ATTRS = (
    "self_function_name", "asset_bucket_name", "asset_bucket_prefix",
    "get_bucket_id", "validate_asset_id", "lookup_asset", "update_asset_type",
    "update_s3_metadata", "write_file_version_history", "s3_client",
    "publish_to_file_indexer_sns", "publish_to_orchestration_bus", "parse_event",
)

SELF_FN = "vams-core-prod5-us-west-2-sqsBucketSynccreated0846-SVn5oBrMvPby"
OTHER_FN = "vams-core-prod5-us-west-2-Api-assetFiles-QyGcpvWpt5On"
ASSET_KEY = "myprefix/x-asset-1/model.glb"


@pytest.fixture(autouse=True)
def _restore_module_attrs():
    m = _load()
    saved = {name: getattr(m, name) for name in _PATCHED_ATTRS}
    yield
    for name, value in saved.items():
        setattr(m, name, value)


def _record(event_name, principal, key=ASSET_KEY, bucket="asset-bucket"):
    """An S3 notification record.

    `principalId` is shaped exactly as a live event delivers it — `AWS:<roleId>:<roleSessionName>`,
    where a Lambda's role session name is its function name. Copied from a real event on the
    deployment rather than invented, because the whole discriminator rests on that third component.
    """
    record = {
        "eventSource": "aws:s3",
        "eventName": event_name,
        "s3": {"bucket": {"name": bucket}, "object": {"key": key}},
    }
    if principal is not None:
        record["userIdentity"] = {"principalId": f"AWS:AROAWYZLKFBUDSFWAVGTG:{principal}"}
    return record


def _wire(m):
    """Wire the handler so a created record reaches per-record processing and both sinks."""
    m.self_function_name = SELF_FN
    m.parse_event = MagicMock(side_effect=lambda e: e)
    m.asset_bucket_name = "asset-bucket"
    m.asset_bucket_prefix = "myprefix/"
    m.get_bucket_id = MagicMock(return_value="bucket-1")
    m.validate_asset_id = MagicMock(return_value=True)
    m.lookup_asset = MagicMock(return_value={"databaseId": "db1", "assetId": "x-asset-1"})
    m.update_asset_type = MagicMock(return_value=True)
    m.update_s3_metadata = MagicMock(return_value=True)
    m.write_file_version_history = MagicMock()
    # head_object is called only on the history path; a MagicMock return is enough because the
    # history writer itself is mocked.
    m.s3_client = MagicMock()
    m.publish_to_file_indexer_sns = MagicMock()
    m.publish_to_orchestration_bus = MagicMock()


@pytest.mark.unit
class TestEchoDiscriminator:
    """`is_vams_metadata_stamp_echo` in isolation — both arms, because a predicate that answers True
    too often silently drops real ingestion and one that answers False too often changes nothing."""

    def test_recognizes_this_functions_own_copy(self):
        m = _load()
        m.self_function_name = SELF_FN
        assert m.is_vams_metadata_stamp_echo(_record("ObjectCreated:Copy", SELF_FN)) is True

    @pytest.mark.parametrize("event_name,principal,why", [
        # A Put is never how this handler writes, whatever its metadata already says. This is the arm
        # that keeps a re-upload of an already-stamped file flowing: uploadFile stamps databaseid /
        # assetid / vams-changesource itself, so "metadata matches" is true for real uploads too.
        ("ObjectCreated:Put", SELF_FN, "a Put is not a stamp"),
        # The owner's constraint: bucket sync must keep seeing COPY so it can report a file that was
        # moved, copied or updated. A copy by any other principal is a real change at the new key.
        ("ObjectCreated:Copy", OTHER_FN, "a copy by another VAMS handler is real work"),
        ("ObjectCreated:Copy", "some-external-role", "a copy by an outside principal is real work"),
        # A stamp cannot produce a delete, and a delete must always reach the indexers so they can
        # remove their record.
        ("ObjectRemoved:Delete", SELF_FN, "a delete is never a stamp"),
        ("ObjectRemoved:DeleteMarkerCreated", SELF_FN, "a delete marker is never a stamp"),
        # An event with no userIdentity cannot be attributed, so it must be treated as real.
        ("ObjectCreated:Copy", None, "unattributable events are treated as real"),
    ])
    def test_does_not_recognize_other_events(self, event_name, principal, why):
        m = _load()
        m.self_function_name = SELF_FN
        assert m.is_vams_metadata_stamp_echo(_record(event_name, principal)) is False, why

    def test_matches_the_session_component_not_a_substring(self):
        # The trap a substring test would fall into. `principalId` is colon-delimited, so comparing
        # with `in principal_id` would match a DIFFERENT function whose session name merely contains
        # this one's — and would then discard that function's genuine copy. Neither direction of this
        # pair is safe to drop, which is why the check splits on ':'.
        m = _load()
        m.self_function_name = SELF_FN
        longer = _record("ObjectCreated:Copy", SELF_FN + "-and-more")
        assert m.is_vams_metadata_stamp_echo(longer) is False
        # Control: the same record with the exact session name IS recognized, so the assertion above
        # is about the comparison and not about the record being malformed.
        assert m.is_vams_metadata_stamp_echo(_record("ObjectCreated:Copy", SELF_FN)) is True

    def test_degrades_to_forwarding_when_the_function_name_is_unavailable(self):
        # AWS_LAMBDA_FUNCTION_NAME is always set by the runtime, but if it ever were not, the safe
        # direction is to treat the event as real: an extra index pass costs work, a dropped one
        # loses a file from search.
        m = _load()
        m.self_function_name = ""
        assert m.is_vams_metadata_stamp_echo(_record("ObjectCreated:Copy", SELF_FN)) is False


@pytest.mark.unit
class TestEchoDoesNoWork:
    """The echo must reach NO sink and perform NO write. Asserting only "the indexer was not called"
    would pass while the handler still re-stamped the object, recomputed the asset type and wrote a
    second history row — which is the data-visible half of the defect."""

    def test_echo_touches_nothing(self):
        m = _load()
        _wire(m)
        success, should_index, message = m.process_s3_record(_record("ObjectCreated:Copy", SELF_FN))

        # Processed successfully, so the SQS message is DELETED rather than redelivered. A skip
        # reported as a failure is how a message ends up cycling in the first place.
        assert success is True
        assert should_index is False
        assert "metadata stamp echo" in message
        m.update_s3_metadata.assert_not_called()
        m.update_asset_type.assert_not_called()
        m.write_file_version_history.assert_not_called()
        # Exits before the asset lookup too, so an echo costs no DynamoDB read.
        m.lookup_asset.assert_not_called()

    def test_the_same_key_as_a_put_is_fully_processed(self):
        # The paired positive, and the one that makes the test above mean anything: without it, a
        # handler that skipped this key for ANY reason — a bad prefix, a reserved segment, a
        # validation miss — would satisfy the assertions above.
        m = _load()
        _wire(m)
        success, should_index, message = m.process_s3_record(_record("ObjectCreated:Put", SELF_FN))

        assert success is True
        assert should_index is True
        m.update_s3_metadata.assert_called_once()
        m.update_asset_type.assert_called_once()
        m.lookup_asset.assert_called_once()

    def test_a_copy_by_another_handler_is_fully_processed(self):
        # The owner's explicit constraint, as an end-to-end arm rather than a predicate arm: a file
        # copy or move performed elsewhere in VAMS must still be stamped, typed and forwarded.
        m = _load()
        _wire(m)
        success, should_index, _ = m.process_s3_record(_record("ObjectCreated:Copy", OTHER_FN))

        assert success is True
        assert should_index is True
        m.update_s3_metadata.assert_called_once()


@pytest.mark.unit
class TestOneUploadFiresEachSinkOnce:
    """The behaviour users see. One upload produces two notifications — the write and this handler's
    stamp — and before the fix both reached the file indexer and the fileUpload trigger, so a workflow
    with a fileUpload trigger ran twice for one uploaded file."""

    def test_batch_of_put_then_echo_forwards_one_record(self):
        m = _load()
        _wire(m)
        event = {"Records": [
            _record("ObjectCreated:Put", SELF_FN),
            _record("ObjectCreated:Copy", SELF_FN),   # the stamp this handler made for that Put
        ]}
        m.lambda_handler_created(event, MagicMock())

        # Both sinks fire once for the batch...
        m.publish_to_file_indexer_sns.assert_called_once()
        m.publish_to_orchestration_bus.assert_called_once()
        # ...and — the assertion that actually distinguishes fixed from unfixed — carry ONE record.
        # A per-sink call count cannot see the difference, because the handler publishes one batched
        # event either way; the record COUNT inside it is what doubled.
        (published_records,) = m.publish_to_orchestration_bus.call_args[0]
        assert len(published_records) == 1
        assert published_records[0]["eventName"] == "ObjectCreated:Put"

    def test_two_distinct_uploads_still_forward_two_records(self):
        # The control for the count above: the fix must remove the ECHO, not collapse records that
        # happen to share a batch. Two real Puts stay two records.
        m = _load()
        _wire(m)
        event = {"Records": [
            _record("ObjectCreated:Put", SELF_FN, key="myprefix/x-asset-1/a.glb"),
            _record("ObjectCreated:Put", SELF_FN, key="myprefix/x-asset-1/b.glb"),
        ]}
        m.lambda_handler_created(event, MagicMock())

        (published_records,) = m.publish_to_orchestration_bus.call_args[0]
        assert len(published_records) == 2
